"""The tray app: an icon with Back up now, Pause, Settings and Restore, running backups in the background.

Tk owns the main thread (the settings window); the icon runs detached (pystray) and backups run on a thread of
their own (watch.Runner). Anything they want shown goes through a queue the Tk loop empties.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, simpledialog, ttk

from mnemerynne_backup import __version__, autostart, signin
from mnemerynne_backup.backup import Backup, BackupError, connect
from mnemerynne_backup.cli import describe
from mnemerynne_backup.config import APP, COMMON_EXCLUDES, Config, state_dir
from mnemerynne_backup.engine import Report
from mnemerynne_backup.transport import TransportError
from mnemerynne_backup.watch import Runner

log = logging.getLogger(__name__)

COLORS = {"ok": "#e0569b", "busy": "#4b8fe2", "problem": "#d9822b", "paused": "#8a8a8a"}


# ------------------------------------------------------------------ helpers
def _icon_image(state: str):
    """A rounded square in the state's colour with an arrow going up."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=COLORS[state])
    d.polygon([(32, 12), (50, 32), (39, 32), (39, 52), (25, 52), (25, 32), (14, 32)], fill="white")
    return img


def _single_instance():
    """A lock only one running copy can hold; None when another copy already has it."""
    path = state_dir() / "tray.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _log_to_file() -> None:
    path = state_dir() / "backup.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


# --------------------------------------------------------------------- app
class TrayApp:
    def __init__(self):
        self.config = Config.load()
        self.backup = Backup(self.config)
        self.runner: Runner | None = None
        self.events: queue.Queue = queue.Queue()
        self.reports: list[Report] = []
        self.last_problem: str | None = None
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP)
        self.settings_window: tk.Toplevel | None = None
        self.icon = None

    # ------------------------------------------------------------ the icon
    def _status_text(self, *_):
        if not self.config.server:
            return "Not signed in"
        if self.runner and self.runner.paused:
            return "Paused"
        if self.runner and self.runner.busy:
            return "Backing up…"
        problems = [r for r in self.reports if not r.ok]
        if problems:
            return describe(problems[0])
        if self.reports:
            return "Up to date · " + time.strftime("%H:%M", time.localtime(max(r.finished_at for r in self.reports)))
        return "Starting…"

    def _icon_state(self) -> str:
        if self.runner and self.runner.paused:
            return "paused"
        if self.runner and self.runner.busy:
            return "busy"
        if not self.config.server or any(not r.ok for r in self.reports):
            return "problem"
        return "ok"

    def _make_icon(self):
        import pystray
        from pystray import Menu, MenuItem as Item

        def later(fn):
            return lambda icon, item: self.events.put(fn)

        menu = Menu(
            Item(self._status_text, None, enabled=False),
            Menu.SEPARATOR,
            Item("Back up now", later(self.back_up_now)),
            Item("Pause", later(self.toggle_pause), checked=lambda item: bool(self.runner and self.runner.paused)),
            Item("Settings…", later(self.open_settings), default=True),
            Item("Restore…", later(self.open_restore)),
            Item("Open Mnemerynne", later(lambda: webbrowser.open(self.config.server or "about:blank"))),
            Menu.SEPARATOR,
            Item("Quit", later(self.quit)),
        )
        self.icon = pystray.Icon("mnemerynne-backup", _icon_image("problem"), APP, menu)
        self.icon.run_detached()

    def _refresh_icon(self) -> None:
        """Redraw the icon when what it shows changed (not on every tick: some trays flicker)."""
        if self.icon is None:
            return
        shown = (self._icon_state(), self._status_text())
        if shown == getattr(self, "_shown", None):
            return
        self._shown = shown
        self.icon.icon = _icon_image(shown[0])
        self.icon.title = f"{APP}: {shown[1]}"
        self.icon.update_menu()

    def _notify(self, text: str) -> None:
        try:
            self.icon.notify(text, APP)
        except Exception:          # not every desktop shows notifications
            log.info("Notification: %s", text)

    # ------------------------------------------------------------- backups
    def start_runner(self) -> None:
        if self.runner is not None or not self.config.server:
            return

        def reported(reports):
            self.events.put(lambda: self._reported(reports))

        self.runner = Runner(self.backup, reported)
        threading.Thread(target=self.runner.loop, name="backups", daemon=True).start()

    def _reported(self, reports: list[Report]) -> None:
        self.reports = reports
        problems = [describe(r) for r in reports if not r.ok]
        problem = problems[0] if problems else None
        if problem and problem != self.last_problem:
            self._notify(problem)
        self.last_problem = problem
        self._refresh_icon()
        if self.settings_window is not None:
            self._fill_folders()

    def back_up_now(self) -> None:
        if self.runner:
            self.runner.run_now()

    def toggle_pause(self) -> None:
        if self.runner:
            self.runner.paused = not self.runner.paused
            self._refresh_icon()

    def quit(self) -> None:
        if self.runner:
            self.runner.stop()
        if self.icon:
            self.icon.stop()
        self.root.quit()

    def _pump(self) -> None:
        """Run what the icon and the backup thread asked for, on the Tk thread; refresh the icon now and then."""
        try:
            while True:
                self.events.get_nowait()()
        except queue.Empty:
            pass
        except Exception as exc:
            log.exception("UI action failed")
            messagebox.showerror(APP, str(exc))
        self._refresh_icon()
        self.root.after(500, self._pump)

    def _in_background(self, work, done) -> None:
        """Run work() off the Tk thread (network calls), then done(result or exception) on it."""
        def go():
            try:
                result = work()
            except Exception as exc:
                result = exc
            self.events.put(lambda: done(result))
        threading.Thread(target=go, daemon=True).start()

    # ------------------------------------------------------------- sign in
    def sign_in(self, use_token: bool = False) -> None:
        server = simpledialog.askstring(APP, "Mnemerynne's address:", initialvalue=self.config.server or "https://",
                                        parent=self._parent())
        if not server:
            return
        server = server.strip().rstrip("/")
        token = None
        if use_token:
            token = simpledialog.askstring(APP, "Paste the sync token from Mnemerynne → Preferences → Backups:",
                                           show="•", parent=self._parent())
            if not token:
                return

        def work():
            info = connect(server)
            if token:
                signin.save_token(server, token)
            else:
                signin.browser_sign_in(server, self.config.computer)
            return info

        def done(result):
            if isinstance(result, Exception):
                messagebox.showerror(APP, f"Signing in didn't work: {result}", parent=self._parent())
                return
            self.config.server, self.config.api_url = server, result.get("api_url") or server
            self.backup = Backup(self.config, self.backup.state)
            self.backup.save()
            if self.runner:
                self.runner.backup = self.backup
            self.start_runner()
            if self.settings_window is not None:
                self._fill_settings()
            self._refresh_icon()

        self._in_background(work, done)

    def sign_out(self) -> None:
        if not messagebox.askyesno(APP, "Sign this computer out? Backups stop until you sign in again.",
                                   parent=self._parent()):
            return
        if self.runner:
            self.runner.stop()
            self.runner = None
        signin.sign_out(self.config.server)
        self.config.server = self.config.api_url = ""
        self.backup = Backup(self.config, self.backup.state)
        self.backup.save()
        self._fill_settings()

    def _parent(self):
        return self.settings_window or self.root

    # ------------------------------------------------------------ settings
    def open_settings(self) -> None:
        if self.settings_window is not None:
            self.settings_window.deiconify()
            self.settings_window.lift()
            return
        w = self.settings_window = tk.Toplevel(self.root)
        w.title(f"{APP} {__version__}")
        w.minsize(560, 420)
        w.protocol("WM_DELETE_WINDOW", self._close_settings)
        pad = {"padx": 10, "pady": 6}

        top = ttk.Frame(w)
        top.pack(fill="x", **pad)
        self.account = ttk.Label(top)
        self.account.pack(side="left")
        self.sign_button = ttk.Button(top)
        self.sign_button.pack(side="right")
        self.token_button = ttk.Button(top, text="Use a token…", command=lambda: self.sign_in(True))
        self.token_button.pack(side="right", padx=6)

        box = ttk.LabelFrame(w, text="Backed-up folders")
        box.pack(fill="both", expand=True, **pad)
        cols = ("label", "path", "status")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", height=7, selectmode="browse")
        for col, title, width in (("label", "On the NAS", 120), ("path", "On this computer", 220),
                                  ("status", "Last pass", 220)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, stretch=True)
        self.tree.pack(fill="both", expand=True, padx=6, pady=6)
        buttons = ttk.Frame(box)
        buttons.pack(fill="x", padx=6, pady=(0, 6))
        for text, fn in (("Add folder…", self.add_folder), ("Remove", self.remove_folder),
                         ("Pause / resume", self.pause_folder), ("Leave out…", self.edit_excludes),
                         ("Searchable…", self.toggle_searchable), ("Skipped files…", self.show_skipped)):
            ttk.Button(buttons, text=text, command=fn).pack(side="left", padx=(0, 6))

        opts = ttk.LabelFrame(w, text="Options")
        opts.pack(fill="x", **pad)
        self.limit = tk.StringVar(value=str(self.config.bandwidth_kbps or ""))
        ttk.Label(opts, text="Upload limit (KB/s, empty for none):").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(opts, textvariable=self.limit, width=8).grid(row=0, column=1, sticky="w")
        self.metered = tk.BooleanVar(value=self.config.pause_on_metered)
        ttk.Checkbutton(opts, text="Wait while on a metered connection (a phone's hotspot)",
                        variable=self.metered).grid(row=1, column=0, columnspan=2, sticky="w", padx=6)
        self.autostart = tk.BooleanVar(value=autostart.enabled())
        ttk.Checkbutton(opts, text="Start when I sign in to this computer",
                        variable=self.autostart).grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))
        bottom = ttk.Frame(w)
        bottom.pack(fill="x", **pad)
        ttk.Button(bottom, text="Save", command=self._save_options).pack(side="right")
        ttk.Button(bottom, text="Restore…", command=self.open_restore).pack(side="left")
        self._fill_settings()

    def _close_settings(self) -> None:
        self.settings_window.destroy()
        self.settings_window = None

    def _fill_settings(self) -> None:
        if self.settings_window is None:
            return
        if self.config.server:
            self.account.config(text=f"Signed in to {self.config.server} as {self.config.computer}")
            self.sign_button.config(text="Sign out", command=self.sign_out)
        else:
            self.account.config(text="Not signed in")
            self.sign_button.config(text="Sign in…", command=self.sign_in)
        self._fill_folders()

    def _fill_folders(self) -> None:
        last = {r.label: r for r in self.reports}
        stored = self.backup.last_reports()
        self.tree.delete(*self.tree.get_children())
        for f in self.config.folders:
            r = last.get(f.label) or stored.get(f.label)
            status = "paused" if f.paused else (describe(r).split(": ", 1)[1] if r else "not yet")
            self.tree.insert("", "end", iid=f.label, values=(f.label, f.path, status))

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP, "Pick a folder in the list first.", parent=self.settings_window)
            return None
        return self.config.folder(sel[0])

    def _changed(self) -> None:
        self._fill_folders()
        if self.runner:
            self.runner.restart_watch()
            self.runner.run_now()

    def add_folder(self) -> None:
        if not self.config.server:
            messagebox.showinfo(APP, "Sign in first.", parent=self.settings_window)
            return
        path = filedialog.askdirectory(parent=self.settings_window, title="Folder to back up")
        if not path:
            return
        label = simpledialog.askstring(APP, "Its name on the NAS (under Backups\\" + self.config.computer + "):",
                                       initialvalue=os.path.basename(path.rstrip("/\\")) or "Drive",
                                       parent=self.settings_window)
        if not label:
            return

        def done(result):
            if isinstance(result, Exception):
                messagebox.showerror(APP, str(result), parent=self.settings_window)
            else:
                self._changed()
        self._in_background(lambda: self.backup.add(path, label), done)

    def remove_folder(self) -> None:
        f = self._selected()
        if f and messagebox.askyesno(APP, f"Stop backing up {f.path}?\n\nIts copy on the NAS stays until you "
                                          f"delete it in Files.", parent=self.settings_window):
            self.backup.remove(f.label)
            self._changed()

    def pause_folder(self) -> None:
        f = self._selected()
        if f:
            self.backup.set_paused(f.label, not f.paused)
            self._changed()

    def edit_excludes(self) -> None:
        f = self._selected()
        if not f:
            return
        offered = ", ".join(COMMON_EXCLUDES)
        text = simpledialog.askstring(
            APP, f"Leave out of {f.label} (names or patterns, separated by commas; e.g. {offered}, *.iso):",
            initialvalue=", ".join(f.excludes), parent=self.settings_window)
        if text is not None:
            self.backup.set_excludes(f.label, [p.strip() for p in text.split(",") if p.strip()])
            self._changed()

    def toggle_searchable(self) -> None:
        f = self._selected()
        if not f:
            return
        on = messagebox.askyesnocancel(
            APP, f"Let Erynne search the backup of {f.label}?\n\nYes: its documents are read for search (in the "
                 f"background). No: they're kept but not searchable.", parent=self.settings_window)
        if on is None:
            return

        def done(result):
            if isinstance(result, Exception):
                messagebox.showerror(APP, str(result), parent=self.settings_window)
            else:
                messagebox.showinfo(APP, f"{f.label} is " + ("searchable now." if result["searchable"] else
                                                              "not searchable."), parent=self.settings_window)
        self._in_background(lambda: self.backup.set_searchable(f.label, on), done)

    def show_skipped(self) -> None:
        f = self._selected()
        if not f:
            return
        r = next((r.json() for r in self.reports if r.label == f.label), None) or \
            self.backup.last_reports().get(f.label) or {}
        lines = [f"failed  {p}: {why}" for p, why in r.get("errors", [])] + \
                [f"skipped {p}: {why}" for p, why in r.get("skipped", [])]
        top = tk.Toplevel(self.settings_window)
        top.title(f"{f.label}: not backed up")
        text = tk.Text(top, width=100, height=24, wrap="none")
        text.insert("1.0", "\n".join(lines) or "Everything in this folder was backed up.")
        text.config(state="disabled")
        text.pack(fill="both", expand=True)

    def _save_options(self) -> None:
        limit = self.limit.get().strip()
        if limit and not limit.isdigit():
            messagebox.showerror(APP, "The upload limit is a number of KB a second.", parent=self.settings_window)
            return
        self.config.bandwidth_kbps = int(limit or 0)
        self.config.pause_on_metered = self.metered.get()
        self.backup.save()
        if self.backup._transport is not None:
            self.backup._transport.throttle.rate = self.config.bandwidth_kbps * 1024
        if self.autostart.get() != autostart.enabled():
            autostart.set_enabled(self.autostart.get())

    # ------------------------------------------------------------- restore
    def open_restore(self) -> None:
        if not self.config.folders:
            messagebox.showinfo(APP, "Nothing is backed up yet.", parent=self._parent())
            return
        w = tk.Toplevel(self.root)
        w.title("Restore")
        pad = {"padx": 10, "pady": 4}
        ttk.Label(w, text="Backed-up folder:").grid(row=0, column=0, sticky="w", **pad)
        which = ttk.Combobox(w, values=[f.label for f in self.config.folders], state="readonly")
        which.current(0)
        which.grid(row=0, column=1, sticky="we", **pad)
        ttk.Label(w, text="Only this file or folder in it (optional):").grid(row=1, column=0, sticky="w", **pad)
        part = ttk.Entry(w, width=40)
        part.grid(row=1, column=1, sticky="we", **pad)
        ttk.Label(w, text="Files already at the place you pick are left as they are.",
                  foreground="#666").grid(row=2, column=0, columnspan=2, sticky="w", **pad)

        def go():
            dest = filedialog.askdirectory(parent=w, title="Where to put it")
            if not dest:
                return
            label, path = which.get(), part.get().strip().replace("\\", "/") or None
            w.destroy()

            def done(result):
                if isinstance(result, Exception):
                    messagebox.showerror(APP, f"Restoring didn't work: {result}")
                else:
                    messagebox.showinfo(APP, f"Restored {result} file{'s' if result != 1 else ''} to {dest}.")
            self._in_background(lambda: self.backup.restore(label, dest, path), done)

        ttk.Button(w, text="Restore to…", command=go).grid(row=3, column=1, sticky="e", **pad)

    # ----------------------------------------------------------------- run
    def run(self) -> None:
        self._make_icon()
        self.start_runner()
        if not self.config.server:
            self.open_settings()
        self.root.after(500, self._pump)
        self.root.mainloop()


def main() -> int:
    lock = _single_instance()
    if lock is None:
        print(f"{APP} is already running (look for its icon).", file=sys.stderr)
        return 0
    _log_to_file()
    code = 0
    try:
        TrayApp().run()
    except (BackupError, TransportError) as exc:
        log.exception("Stopped")
        messagebox.showerror(APP, str(exc))
        code = 1
    logging.shutdown()
    # pystray's setup thread isn't a daemon and waits for ever where no tray appeared (GNOME without the
    # AppIndicator extension); backups are already stopped, so leave without waiting for it.
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
