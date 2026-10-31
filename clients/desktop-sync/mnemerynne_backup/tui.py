"""The terminal app: the tray's Back up now, Pause, Settings and Restore in one sakura-pink screen.

For a laptop without a tray (a tiling window manager, a console): `mnemerynne-backup-tui`. Backups run on a thread
of their own (watch.Runner) while the screen is open, and stop when it closes. It takes the tray's lock, so the two
never back up at once.
"""

from __future__ import annotations

import logging
import math
import os
import random
import sys
import time
import webbrowser

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import Button, Checkbox, DataTable, Footer, Input, Label, RichLog, Select, Static

from mnemerynne_backup import __version__, signin
from mnemerynne_backup.backup import Backup, connect, update_offer
from mnemerynne_backup.cli import describe
from mnemerynne_backup.config import APP, COMMON_EXCLUDES, Config
from mnemerynne_backup.engine import Report
from mnemerynne_backup.watch import Runner, log_to_file, single_instance

log = logging.getLogger(__name__)

# The dark neon sakura palette (dotfiles/palette/sakura.sh).
BG, SURFACE, FG = "#0a0610", "#1a0f1f", "#f3d6ec"
PINK, MAGENTA, VIOLET, CYAN, PETAL, WARN, CRIT = ("#ff2a9d", "#ff3df2", "#b026ff", "#00f0ff", "#8a3a63",
                                                  "#ffe600", "#ff1f5a")
SAKURA = Theme(name="sakura", primary=PINK, secondary=VIOLET, accent=MAGENTA, foreground=FG, background=BG,
               surface=SURFACE, panel=SURFACE, warning=WARN, error=CRIT, success=CYAN, dark=True)
STATE_COLORS = {"ok": PINK, "busy": CYAN, "problem": WARN, "paused": PETAL}
GLYPHS = "❀✿✾·❀✿"            # one cell wide each, so they drift cleanly on any terminal


# ------------------------------------------------------------------ petals
class Petals(Widget):
    """Petals drifting down behind the title: more while backing up, still while paused."""

    DEFAULT_CSS = "Petals { height: 5; }"

    def __init__(self):
        super().__init__()
        self.petals: list[list] = []          # [x, y, speed, sway phase, glyph, colour]
        self.mood = "ok"
        self.subtitle = ""

    def on_mount(self) -> None:
        self.set_interval(0.12, self._fall)

    def _fall(self) -> None:
        width, height = self.size
        if width <= 0 or self.mood == "paused":
            return
        rate = width / (25 if self.mood == "busy" else 70)
        while rate > 0:
            if random.random() < rate:
                self.petals.append([random.uniform(0, width), -1.0, random.uniform(0.15, 0.4),
                                    random.uniform(0, math.tau), random.choice(GLYPHS),
                                    random.choice((PINK, PINK, MAGENTA, PETAL, FG))])
            rate -= 1
        speed = 1.8 if self.mood == "busy" else 1.0
        for p in self.petals:
            p[1] += p[2] * speed
            p[3] += 0.25
            p[0] += math.sin(p[3]) * 0.35 + 0.12 * speed      # a soft breeze to the right
        self.petals = [p for p in self.petals if p[1] < height and -1 < p[0] < width + 1]
        self.refresh()

    def render(self) -> Text:
        width, height = self.size
        grid: list[list[tuple[str, str]]] = [[(" ", "")] * width for _ in range(height)]
        for x, y, _, _, glyph, colour in self.petals:
            col, row = int(x), int(y)
            if 0 <= row < height and 0 <= col < width:
                grid[row][col] = (glyph, colour)
        for row, (words, style) in ((height // 2 - 1, (" ✿ mnemerynne backup ✿ ", f"bold {PINK}")),
                                    (height // 2, (self.subtitle, MAGENTA))):
            start = max(0, (width - len(words)) // 2)
            for i, ch in enumerate(words[:width]):
                grid[row][start + i] = (ch, style)
        text = Text(no_wrap=True, overflow="crop")
        for i, row in enumerate(grid):
            for ch, style in row:
                text.append(ch, style)
            if i < height - 1:
                text.append("\n")
        return text


# ------------------------------------------------------------------ dialogs
class PathSuggester(Suggester):
    """Completes folder names as you type (→ takes the suggestion)."""

    def __init__(self):
        super().__init__(use_cache=False, case_sensitive=True)

    async def get_suggestion(self, value: str) -> str | None:
        head, tail = os.path.split(os.path.expanduser(value))
        if not head or not value.endswith(tail):
            return None
        try:
            names = sorted(n for n in os.listdir(head) if n.startswith(tail) and os.path.isdir(os.path.join(head, n))
                           and (tail.startswith(".") or not n.startswith(".")))
        except OSError:
            return None
        return value[:len(value) - len(tail)] + names[0] + "/" if names and names[0] != tail else None


class Dialog(ModalScreen):
    """A pink-bordered box in the middle; Esc closes it with nothing."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = f"""
    Dialog {{ align: center middle; background: {BG} 70%; }}
    Dialog > Vertical {{ width: 76; max-width: 100%; height: auto; max-height: 90%; padding: 1 2;
                         border: round {PINK}; background: {SURFACE}; }}
    Dialog Label {{ margin-top: 1; color: {FG}; }}
    Dialog .hint {{ color: {PETAL}; }}
    Dialog Horizontal {{ height: auto; margin-top: 1; align-horizontal: right; }}
    Dialog Button {{ margin-left: 1; }}
    """
    title_text = APP

    def action_cancel(self) -> None:
        self.dismiss(None)

    def boxed(self, *children: Widget) -> Vertical:
        box = Vertical(*children)
        box.border_title = f"🌸 {self.title_text}"
        return box


class Ask(Dialog):
    """One line of text; Enter gives it back."""

    def __init__(self, title: str, prompt: str, initial: str = "", *, password: bool = False, hint: str = "",
                 suggester: Suggester | None = None):
        super().__init__()
        self.title_text, self.prompt, self.initial, self.password, self.hint = title, prompt, initial, password, hint
        self.suggester = suggester

    def compose(self) -> ComposeResult:
        yield self.boxed(Label(self.prompt), Input(self.initial, password=self.password, suggester=self.suggester),
                         Static(self.hint, classes="hint"),
                         Horizontal(Button("Cancel", id="cancel"), Button("OK", variant="primary", id="ok")))

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def ok(self) -> None:
        self.dismiss(self.query_one(Input).value)

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class Choose(Dialog):
    """A question with buttons; gives back the chosen button's value."""

    def __init__(self, title: str, question: str, choices: list[tuple[str, object]]):
        super().__init__()
        self.title_text, self.question, self.choices = title, question, choices

    def compose(self) -> ComposeResult:
        buttons = [Button(text, variant="primary" if i == len(self.choices) - 1 else "default", id=f"c{i}")
                   for i, (text, _) in enumerate(self.choices)]
        yield self.boxed(Static(self.question), Horizontal(*buttons))

    def on_mount(self) -> None:
        self.query(Button).last().focus()

    @on(Button.Pressed)
    def chosen(self, event: Button.Pressed) -> None:
        self.dismiss(self.choices[int(event.button.id[1:])][1])


class Lines(Dialog):
    """Text to read (the skipped files); Esc or Close."""

    def __init__(self, title: str, lines: list[str]):
        super().__init__()
        self.title_text, self.lines = title, lines

    def compose(self) -> ComposeResult:
        yield self.boxed(VerticalScroll(Static("\n".join(self.lines)), id="lines"),
                         Horizontal(Button("Close", variant="primary")))

    def on_mount(self) -> None:
        self.query_one("#lines").styles.max_height = 20
        self.query_one(Button).focus()

    @on(Button.Pressed)
    def close(self) -> None:
        self.dismiss(None)


class AddFolder(Dialog):
    title_text = "Back up a folder"

    def __init__(self, computer: str):
        super().__init__()
        self.computer = computer

    def compose(self) -> ComposeResult:
        yield self.boxed(
            Label("Folder on this computer:"), Input(placeholder="~/Documents", suggester=PathSuggester(), id="path"),
            Static("→ takes the suggested name", classes="hint"),
            Label(f"Its name on the NAS (under Backups\\{self.computer}):"),
            Input(placeholder="the folder's own name", id="label"),
            Horizontal(Button("Cancel", id="cancel"), Button("Back it up", variant="primary", id="ok")))

    @on(Input.Changed, "#path")
    def name_it(self, event: Input.Changed) -> None:
        self.query_one("#label", Input).placeholder = os.path.basename(event.value.rstrip("/")) or "Drive"

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def ok(self) -> None:
        path = self.query_one("#path", Input).value.strip()
        label = self.query_one("#label", Input)
        if path:
            self.dismiss((path, label.value.strip() or label.placeholder))

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class SignIn(Dialog):
    title_text = "Sign in"

    def __init__(self, server: str):
        super().__init__()
        self.server = server

    def compose(self) -> ComposeResult:
        yield self.boxed(
            Label("Mnemerynne's address:"), Input(self.server or "https://", id="server"),
            Label("Sync token (leave empty to sign in in your browser):"), Input(password=True, id="token"),
            Static("Make a token in Mnemerynne → Preferences → Backups.", classes="hint"),
            Horizontal(Button("Cancel", id="cancel"), Button("Sign in", variant="primary", id="ok")))

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def ok(self) -> None:
        server = self.query_one("#server", Input).value.strip().rstrip("/")
        if server and server != "https:":
            self.dismiss((server, self.query_one("#token", Input).value.strip() or None))

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class Options(Dialog):
    title_text = "Options"

    def __init__(self, config: Config):
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        yield self.boxed(
            Label("Upload limit (KB/s, empty for none):"),
            Input(str(self.config.bandwidth_kbps or ""), restrict=r"\d*", id="limit"),
            Checkbox("Wait while on a metered connection (a phone's hotspot)", self.config.pause_on_metered,
                     id="metered"),
            Horizontal(Button("Cancel", id="cancel"), Button("Save", variant="primary", id="ok")))

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def ok(self) -> None:
        self.dismiss((int(self.query_one("#limit", Input).value or 0), self.query_one("#metered", Checkbox).value))

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class Restore(Dialog):
    title_text = "Restore"

    def __init__(self, labels: list[str], selected: str | None):
        super().__init__()
        self.labels, self.selected = labels, selected

    def compose(self) -> ComposeResult:
        yield self.boxed(
            Label("Backed-up folder:"),
            Select([(label, label) for label in self.labels], value=self.selected or self.labels[0],
                   allow_blank=False, id="which"),
            Label("Only this file or folder in it (optional):"), Input(placeholder="Reports/2026", id="part"),
            Label("Where to put it:"), Input("~/restored", suggester=PathSuggester(), id="dest"),
            Static("Files already at the place you pick are left as they are.", classes="hint"),
            Horizontal(Button("Cancel", id="cancel"), Button("Restore", variant="primary", id="ok")))

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def ok(self) -> None:
        dest = self.query_one("#dest", Input).value.strip()
        part = self.query_one("#part", Input).value.strip().replace("\\", "/") or None
        if dest:
            self.dismiss((self.query_one("#which", Select).value, part, dest))

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------- app
class Reported(Message):
    """A pass finished (sent from the backup thread)."""

    def __init__(self, reports: list[Report]):
        super().__init__()
        self.reports = reports


class BackupTui(App):
    TITLE = APP
    CSS = f"""
    Screen {{ background: {BG}; }}
    #status {{ height: 1; padding: 0 2; background: {SURFACE}; }}
    #folders {{ height: 1fr; margin: 1 1 0 1; border: round {PINK}; border-title-color: {PINK};
                background: {BG}; }}
    #folders > .datatable--header {{ background: {BG}; color: {MAGENTA}; text-style: bold; }}
    #folders > .datatable--cursor {{ background: {PINK} 35%; color: {FG}; }}
    #activity {{ height: 9; margin: 0 1; border: round {VIOLET}; border-title-color: {VIOLET}; background: {BG};
                 scrollbar-size-vertical: 1; }}
    """
    BINDINGS = [
        Binding("b", "back_up_now", "Back up now"),
        Binding("p", "toggle_pause", "Pause all"),
        Binding("a", "add_folder", "Add"),
        Binding("x,delete", "remove_folder", "Remove"),
        Binding("space", "pause_folder", "Pause folder"),
        Binding("e", "edit_excludes", "Leave out"),
        Binding("s", "toggle_searchable", "Searchable"),
        Binding("k", "show_skipped", "Skipped"),
        Binding("r", "restore", "Restore"),
        Binding("o", "options", "Options"),
        Binding("l", "sign_in_out", "Sign in/out"),
        Binding("w", "open_web", "Open Mnemerynne", show=False),
        Binding("u", "get_update", "Update", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.config = Config.load()
        self.backup = Backup(self.config)
        self.runner: Runner | None = None
        self.reports: list[Report] = []
        self.last_problem: str | None = None
        self.update: tuple[str, str] | None = None      # (version, download address) when one is offered
        self.was_busy = False

    def compose(self) -> ComposeResult:
        yield Petals()
        yield Static(id="status")
        table = DataTable(id="folders", cursor_type="row", zebra_stripes=False)
        table.border_title = "🌸 backed-up folders"
        yield table
        activity = RichLog(id="activity", markup=False, wrap=True, max_lines=500)
        activity.border_title = "✿ petals of activity"
        yield activity
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(SAKURA)
        self.theme = "sakura"
        table = self.query_one(DataTable)
        table.add_column("On the NAS", width=18)
        table.add_column("On this computer", width=34)
        table.add_column("Last pass")
        self._fill_folders()
        self._refresh()
        self.set_interval(0.5, self._refresh)
        self.set_timer(60, self.check_update)
        self.set_interval(24 * 3600, self.check_update)
        self.say(f"hello 🌸 {APP} {__version__} on {self.config.computer}", PINK)
        if self.config.server:
            self.start_runner()
        else:
            self.action_sign_in_out()

    # -------------------------------------------------------------- showing
    def say(self, text: str, colour: str = FG) -> None:
        line = Text(time.strftime("%H:%M "), style=PETAL)
        line.append(text, style=colour)
        self.query_one(RichLog).write(line)

    def _state(self) -> str:
        if self.runner and self.runner.paused:
            return "paused"
        if self.runner and self.runner.busy:
            return "busy"
        if not self.config.server or any(not r.ok for r in self.reports):
            return "problem"
        return "ok"

    def _status_text(self) -> str:
        if not self.config.server:
            return "Not signed in: press l"
        if self.runner and self.runner.paused:
            return "Paused: press p to carry on"
        if self.runner and self.runner.busy:
            return "Backing up…"
        problems = [r for r in self.reports if not r.ok]
        if problems:
            return describe(problems[0])
        if self.reports:
            return "Up to date · " + time.strftime("%H:%M", time.localtime(max(r.finished_at for r in self.reports)))
        return "Starting…"

    def _refresh(self) -> None:
        state = self._state()
        busy = state == "busy"
        if busy and not self.was_busy:
            self.say("backing up… ❀", CYAN)
        self.was_busy = busy
        dot = {"ok": "✿", "busy": "❀", "problem": "✾", "paused": "·"}[state]
        status = Text(f"{dot} {self._status_text()}", style=f"bold {STATE_COLORS[state]}")
        if self.config.server:
            status.append(f"   {self.config.computer} → {self.config.server}", style=PETAL)
        if self.update:
            status.append(f"   version {self.update[0]} is ready: press u", style=CYAN)
        self.query_one("#status", Static).update(status)
        petals = self.query_one(Petals)
        petals.mood = state
        petals.subtitle = f"~ {self.config.computer} ~"

    def _fill_folders(self) -> None:
        table = self.query_one(DataTable)
        row = table.cursor_row
        last = {r.label: r for r in self.reports}
        stored = self.backup.last_reports()
        table.clear()
        for f in self.config.folders:
            r = last.get(f.label) or stored.get(f.label)
            if f.paused:
                status = Text("paused", style=PETAL)
            elif not r:
                status = Text("not yet", style=PETAL)
            else:
                ok = r.ok if isinstance(r, Report) else r.get("ok", True)
                status = Text(describe(r).split(": ", 1)[1], style=FG if ok else WARN)
            table.add_row(Text(f.label, style=f"bold {PINK}"), Text(f.path, style=FG), status, key=f.label)
        if self.config.folders:
            table.move_cursor(row=min(row, len(self.config.folders) - 1))

    # -------------------------------------------------------------- backups
    def start_runner(self) -> None:
        if self.runner is not None or not self.config.server:
            return
        self.runner = Runner(self.backup, lambda reports: self.post_message(Reported(reports)))
        self.run_worker(self.runner.loop, thread=True, name="backups", exit_on_error=False)

    @on(Reported)
    def reported(self, message: Reported) -> None:
        self.reports = message.reports
        for r in message.reports:
            if r.uploaded or r.moved or r.deleted or not r.ok:
                self.say(describe(r), FG if r.ok else WARN)
        if message.reports and all(r.ok and not (r.uploaded or r.moved or r.deleted) for r in message.reports):
            self.say("all up to date ✿", PINK)
        problems = [describe(r) for r in message.reports if not r.ok]
        problem = problems[0] if problems else None
        if problem and problem != self.last_problem:
            self.notify(problem, title=APP, severity="warning")
        self.last_problem = problem
        self._fill_folders()
        self._refresh()

    def _in_background(self, work, done) -> None:
        """Run work() off the screen's thread (network calls), then done(result or exception) on it."""
        def go():
            try:
                result = work()
            except Exception as exc:
                result = exc
            self.call_from_thread(done, result)
        self.run_worker(go, thread=True, exit_on_error=False)

    def _failed(self, result, what: str) -> bool:
        if isinstance(result, Exception):
            log.warning("%s: %s", what, result)
            self.notify(f"{what}: {result}", title=APP, severity="error")
            self.say(f"{what}: {result}", CRIT)
            return True
        return False

    def _changed(self) -> None:
        self._fill_folders()
        if self.runner:
            self.runner.restart_watch()
            self.runner.run_now()

    def _selected(self):
        if not self.config.folders:
            self.notify("Add a folder first (press a).", title=APP)
            return None
        row = self.query_one(DataTable).cursor_row
        return self.config.folders[min(max(row, 0), len(self.config.folders) - 1)]

    def action_back_up_now(self) -> None:
        if self.runner:
            self.runner.run_now()
            self.say("a pass is on its way ❀", MAGENTA)
        else:
            self.notify("Sign in first (press l).", title=APP)

    def action_toggle_pause(self) -> None:
        if self.runner:
            self.runner.paused = not self.runner.paused
            self.say("paused · petals resting" if self.runner.paused else "carrying on ✿", PETAL)
            self._refresh()

    def action_quit(self) -> None:
        if self.runner:
            self.runner.stop()
        self.exit()

    # -------------------------------------------------------------- sign in
    def action_sign_in_out(self) -> None:
        if not self.config.server:
            self.push_screen(SignIn(self.config.server), self._sign_in)
            return

        def answered(yes):
            if yes:
                self._sign_out()
        self.push_screen(Choose("Sign out", f"Sign {self.config.computer} out of {self.config.server}?\n\n"
                                            f"Backups stop until you sign in again.",
                                [("Stay", False), ("Sign out", True)]), answered)

    def _sign_in(self, answer) -> None:
        if not answer:
            return
        server, token = answer
        self.say(f"signing in to {server}" + ("" if token else ": finish in your browser…"), MAGENTA)

        def work():
            info = connect(server)
            if token:
                signin.save_token(server, token)
            else:
                signin.browser_sign_in(server, self.config.computer)
            return info

        def done(result):
            if self._failed(result, "Signing in didn't work"):
                return
            self.config.server, self.config.api_url = server, result.get("api_url") or server
            self.backup = Backup(self.config, self.backup.state)
            self.backup.save()
            if self.runner:
                self.runner.backup = self.backup
            self.start_runner()
            self.say(f"signed in as {self.config.computer} 🌸", PINK)
            self._fill_folders()
            self._refresh()

        self._in_background(work, done)

    def _sign_out(self) -> None:
        if self.runner:
            self.runner.stop()
            self.runner = None
        signin.sign_out(self.config.server)
        self.config.server = self.config.api_url = ""
        self.backup = Backup(self.config, self.backup.state)
        self.backup.save()
        self.reports = []
        self.say("signed out; backups stopped", PETAL)
        self._fill_folders()
        self._refresh()

    # -------------------------------------------------------------- folders
    def action_add_folder(self) -> None:
        if not self.config.server:
            self.notify("Sign in first (press l).", title=APP)
            return

        def answered(answer):
            if not answer:
                return
            path, label = answer

            def done(result):
                if not self._failed(result, "Adding the folder didn't work"):
                    self.say(f"now backing up {result.path} as {result.label} ✿", PINK)
                    self._changed()
            self._in_background(lambda: self.backup.add(path, label), done)
        self.push_screen(AddFolder(self.config.computer), answered)

    def action_remove_folder(self) -> None:
        f = self._selected()
        if not f:
            return

        def answered(yes):
            if yes:
                self.backup.remove(f.label)
                self.say(f"stopped backing up {f.path}", PETAL)
                self._changed()
        self.push_screen(Choose("Remove", f"Stop backing up {f.path}?\n\nIts copy on the NAS stays until you "
                                          f"delete it in Files.", [("Keep it", False), ("Stop", True)]), answered)

    def action_pause_folder(self) -> None:
        f = self._selected()
        if f:
            self.backup.set_paused(f.label, not f.paused)
            self.say(f"{f.label}: " + ("paused" if f.paused else "carrying on"), PETAL)
            self._changed()

    def action_edit_excludes(self) -> None:
        f = self._selected()
        if not f:
            return

        def answered(text):
            if text is not None:
                self.backup.set_excludes(f.label, [p.strip() for p in text.split(",") if p.strip()])
                self._changed()
        self.push_screen(Ask("Leave out", f"Leave out of {f.label} (names or patterns, separated by commas):",
                             ", ".join(f.excludes), hint=f"e.g. {', '.join(COMMON_EXCLUDES)}, *.iso"), answered)

    def action_toggle_searchable(self) -> None:
        f = self._selected()
        if not f:
            return

        def answered(on):
            if on is None:
                return

            def done(result):
                if not self._failed(result, "Changing Searchable didn't work"):
                    self.say(f"{f.label} is " + ("searchable now ✿" if result["searchable"] else "not searchable"),
                             PINK)
            self._in_background(lambda: self.backup.set_searchable(f.label, on), done)
        self.push_screen(Choose("Searchable", f"Let Erynne search the backup of {f.label}?\n\nYes: its documents are "
                                              f"read for search (in the background). No: they're kept but not "
                                              f"searchable.", [("Cancel", None), ("No", False), ("Yes", True)]),
                         answered)

    def action_show_skipped(self) -> None:
        f = self._selected()
        if not f:
            return
        r = next((r.json() for r in self.reports if r.label == f.label), None) or \
            self.backup.last_reports().get(f.label) or {}
        lines = [f"failed  {p}: {why}" for p, why in r.get("errors", [])] + \
                [f"skipped {p}: {why}" for p, why in r.get("skipped", [])]
        self.push_screen(Lines(f"{f.label}: not backed up", lines or ["Everything in this folder was backed up ✿"]))

    # ------------------------------------------------------- options, restore
    def action_options(self) -> None:
        def answered(answer):
            if not answer:
                return
            self.config.bandwidth_kbps, self.config.pause_on_metered = answer
            self.backup.save()
            if self.backup._transport is not None:
                self.backup._transport.throttle.rate = self.config.bandwidth_kbps * 1024
            self.say("options saved ✿", PINK)
        self.push_screen(Options(self.config), answered)

    def action_restore(self) -> None:
        if not self.config.folders:
            self.notify("Nothing is backed up yet.", title=APP)
            return
        selected = self._selected()

        def answered(answer):
            if not answer:
                return
            label, path, dest = answer
            self.say(f"restoring {label}{'/' + path if path else ''} to {dest}…", MAGENTA)

            def done(result):
                if not self._failed(result, "Restoring didn't work"):
                    self.say(f"restored {result} file{'s' if result != 1 else ''} to {dest} 🌸", PINK)
                    self.notify(f"Restored {result} file{'s' if result != 1 else ''} to {dest}.", title=APP)
            self._in_background(lambda: self.backup.restore(label, dest, path), done)
        self.push_screen(Restore([f.label for f in self.config.folders], selected and selected.label), answered)

    # ------------------------------------------------------- web and updates
    def action_open_web(self) -> None:
        if self.config.server:
            webbrowser.open(self.config.server)

    def check_update(self) -> None:
        """Once a day: does the server offer a newer app? Says so once."""
        def work():
            return update_offer(self.config.server) if self.config.server else None

        def done(result):
            if isinstance(result, tuple) and result != self.update:
                self.update = result
                self.notify(f"Version {result[0]} is ready: press u.", title=APP)
        self._in_background(work, done)

    def action_get_update(self) -> None:
        if self.update:
            webbrowser.open(self.update[1])


def main() -> int:
    lock = single_instance()
    if lock is None:
        print(f"{APP} is already running (the tray icon, or another terminal).", file=sys.stderr)
        return 0
    log_to_file()
    BackupTui().run()
    logging.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
