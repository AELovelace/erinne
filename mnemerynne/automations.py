"""Automator: jobs a person schedules (a morning briefing, a security digest, an API health check).

An automation is a prompt (typed in, or a .md/.txt prompt file read fresh each run), a schedule and a runner:
- erynne: Erynne's agent, with the owner's own access and the tools they picked;
- claude_key: the same tools driven by Claude under the owner's saved key (claude_chat);
- claude_routine: a Claude routine on claude.ai is started (its API trigger) and works through the
  Mnemerynne connector, then reports back with report_automation_run.

Writes only ever become proposals the owner applies, and only when the automation may propose. The
answer goes to the owner's Inbox, a room (read only from what everyone in the room may read), an
email draft or an admin-defined webhook (automations_run.py). This module is the data: who may use it,
schedules, the automations and their runs. Off unless an admin turns it on (meta automations_enabled);
a group may be set (meta automations_group), admins always may.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, time as dtime, timedelta, timezone, tzinfo
from typing import Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mnemerynne import accounts, apis, db, group_refs, users
from mnemerynne.config import Settings

ENABLED_KEY = "automations_enabled"
GROUP_KEY = "automations_group"
RUNNERS = ("erynne", "claude_key", "claude_routine")
TRIGGERS = ("schedule", "manual", "mcp")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_PER_PERSON = 20
MIN_MINUTES = 15
MAX_PROMPT = 8000
MAX_PROMPT_FILE = 60_000                 # characters of instructions taken from a prompt file (a routine gets 65,000)
_FENCE = re.compile(r"^(`{3,}|~{3,})[^\n]*\n(.*?)\n\1[ \t]*$", re.M | re.S)   # a fenced code block, first to last fence
MISSED_GRACE = timedelta(hours=1)        # a run missed by less (the server was down) still runs once
ROUTINE_WAIT = timedelta(hours=2)        # a routine's report must arrive within this
QUIET = "NOTHING_TO_REPORT"              # an answer of just this: nothing delivered
ROUTINE_URL = re.compile(r"^https://api\.anthropic\.com/v1/claude_code/routines/[A-Za-z0-9_\-]{1,100}/fire$")
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_STAMP = "%Y-%m-%d %H:%M:%S"


class AutomationError(ValueError):
    """Refused; the message is safe to show."""


# ------------------------------------------------------------------ access
def enabled(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (ENABLED_KEY,)).fetchone()
    return bool(row and row["value"] == "1")


def set_enabled(conn: sqlite3.Connection, on: bool) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (ENABLED_KEY, "1" if on else "0"))


def group(conn: sqlite3.Connection) -> str:
    """The group(s) whose members may use Automator; '' = admins only."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (GROUP_KEY,)).fetchone()
    return row["value"] if row else ""


def set_group(conn: sqlite3.Connection, name: str) -> str:
    name = group_refs.join(group_refs.split(name))
    if len(name) > 2000:
        raise AutomationError("Pick a group.")
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (GROUP_KEY, name))
    return name


def is_admin(conn: sqlite3.Connection, settings: Settings, user: sqlite3.Row, directory) -> bool:
    return accounts.is_admin(conn, user, directory, settings.admin_usernames)


def may_use(conn: sqlite3.Connection, settings: Settings, user: sqlite3.Row | None, directory) -> bool:
    """Automator is on, and this active person is an admin or in its group."""
    if user is None or not user["active"] or not enabled(conn):
        return False
    return is_admin(conn, settings, user, directory) or group_refs.in_any(conn, user, directory, group(conn))


# --------------------------------------------------------------- schedules
def zone(name: str) -> tzinfo:
    """The time zone; UTC needs no zone database (Windows has none without the tzdata package)."""
    name = (name or "UTC").strip()
    if name.upper() in ("UTC", "ETC/UTC", "Z"):
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise AutomationError(f"“{name}” isn't a time zone (try Europe/London or America/Los_Angeles).") from None


def check_schedule(schedule: dict) -> dict:
    """The schedule in one of two shapes:
    {"every": "day", "at": "07:30", "days": ["mon", ...]}  (no days: every day; "week" = one day)
    {"every": "minutes" | "hours", "n": N}                 (at least MIN_MINUTES apart)
    The second may add "between": ["08:00", "18:00"]: only in that window of the day (its time zone), on the
    window's own beat (08:00, 08:00 + N, … up to 18:00 itself), and with it "days" (the days the window opens
    on; none: every day), so "every hour, 08:00 to 17:00, Mon to Fri" is business hours. A window whose end is
    earlier than its start runs overnight (22:00 to 06:00 the next morning)."""
    if not isinstance(schedule, dict):
        raise AutomationError("Give a schedule.")
    every = str(schedule.get("every", "")).lower()
    if every in ("day", "week"):
        m = _TIME.match(str(schedule.get("at", "")).strip())
        if not m:
            raise AutomationError("Give a time of day as HH:MM, e.g. 07:30.")
        days = [str(d).lower()[:3] for d in (schedule.get("days") or [])]
        if every == "week" and schedule.get("day"):
            days = [str(schedule["day"]).lower()[:3]]
        if any(d not in DAYS for d in days):
            raise AutomationError("Days are mon, tue, wed, thu, fri, sat and sun.")
        days = [d for d in DAYS if d in days] or list(DAYS)
        return {"every": "day", "at": f"{int(m.group(1)):02d}:{m.group(2)}", "days": days}
    if every in ("minutes", "hours"):
        try:
            n = int(schedule.get("n", 0))
        except (TypeError, ValueError):
            raise AutomationError("Say how often, as a number.") from None
        minutes = n * 60 if every == "hours" else n
        if minutes < MIN_MINUTES or minutes > 7 * 24 * 60:
            raise AutomationError(f"Run it at most every {MIN_MINUTES} minutes, and at least once a week.")
        out = {"every": "minutes", "n": minutes}
        between = schedule.get("between")
        if between:                                      # only in a window of the day
            if isinstance(between, dict):                # {"from": "08:00", "to": "18:00"} reads the same
                between = [between.get("from"), between.get("to")]
            if not isinstance(between, (list, tuple)) or len(between) != 2:
                raise AutomationError("Give the window as two times, e.g. between 08:00 and 18:00.")
            times = []
            for t in between:
                m = _TIME.match(str(t or "").strip())
                if not m:
                    raise AutomationError("Give the window's times as HH:MM, e.g. between 08:00 and 18:00.")
                times.append(f"{int(m.group(1)):02d}:{m.group(2)}")
            if times[0] == times[1]:
                raise AutomationError("The window's start and end are the same; leave “Only between” off to run "
                                      "all day.")
            if minutes > 24 * 60:
                raise AutomationError("A window of the day needs it to run at least once a day.")
            out["between"] = times
            days = [str(d).lower()[:3] for d in (schedule.get("days") or [])]   # business hours: mon to fri
            if any(d not in DAYS for d in days):
                raise AutomationError("Days are mon, tue, wed, thu, fri, sat and sun.")
            out["days"] = [d for d in DAYS if d in days] or list(DAYS)   # the days its window opens on
        return out
    raise AutomationError("A schedule runs every day at a time, or every so many minutes or hours.")


def describe(schedule: dict, tz: str) -> str:
    if schedule["every"] == "day":
        days = schedule["days"]
        when = ("every day" if len(days) == 7 else "weekdays" if days == list(DAYS[:5])
                else "weekends" if days == ["sat", "sun"] else ", ".join(d.capitalize() for d in days))
        return f"{when} at {schedule['at']} ({tz})"
    n = schedule["n"]
    every = (("every hour" if n == 60 else f"every {n // 60} hours") if n % 60 == 0
             else f"every {n} minutes")
    if schedule.get("between"):                # "every hour between 08:00 and 17:00, weekdays (America/Los_Angeles)"
        return (f"{every} between {schedule['between'][0]} and {schedule['between'][1]}"
                f"{_days_text(schedule.get('days') or list(DAYS), ', ')} ({tz})")
    return every


def _days_text(days: list[str], lead: str) -> str:
    """"" for every day, else the lead and "weekdays", "weekends" or "Mon, Wed"."""
    if len(days) == 7:
        return ""
    return lead + ("weekdays" if days == list(DAYS[:5]) else "weekends" if days == ["sat", "sun"]
                   else ", ".join(d.capitalize() for d in days))


def _windowed(schedule: dict, tz: str, after: datetime) -> datetime:
    """The next run of an every-N-minutes schedule with a window ("between"): the first of the window's
    times (its start, start + N, … up to its end) strictly after `after`, in UTC. Wall-clock times, so the
    window keeps its hours across a DST change."""
    place = zone(tz)
    step = timedelta(minutes=schedule["n"])
    (h0, m0), (h1, m1) = ((int(x) for x in t.split(":")) for t in schedule["between"])
    days = schedule.get("days") or list(DAYS)
    first = after.astimezone(place).date() - timedelta(days=1)   # yesterday's window may run past midnight
    for offset in range(0, 10):                                  # a week ahead, and then some
        day = first + timedelta(days=offset)
        if DAYS[day.weekday()] not in days:
            continue                                             # its window doesn't open today (a weekend)
        opens = datetime.combine(day, dtime(h0, m0))                       # naive local wall time
        closes = datetime.combine(day, dtime(h1, m1))
        if closes <= opens:
            closes += timedelta(days=1)                                    # overnight: ends the next morning
        slot = opens
        while slot <= closes:
            at = slot.replace(tzinfo=place).astimezone(timezone.utc)
            if at > after:
                return at.replace(microsecond=0)
            slot += step
    raise AutomationError("That schedule never runs.")


def next_run(schedule: dict, tz: str, after: datetime) -> datetime:
    """The first time strictly after `after` (aware, any zone) that the schedule runs, in UTC."""
    after = after.astimezone(timezone.utc)
    if schedule["every"] == "minutes":
        if schedule.get("between"):
            return _windowed(schedule, tz, after)
        return (after + timedelta(minutes=schedule["n"])).replace(microsecond=0)
    place = zone(tz)
    hour, minute = (int(x) for x in schedule["at"].split(":"))
    start = after.astimezone(place).date()
    for offset in range(0, 9):
        day = start + timedelta(days=offset)
        if DAYS[day.weekday()] not in schedule["days"]:
            continue
        # A wall-clock time skipped by a DST change runs at the moment after the gap (fold=0, normalised).
        local = datetime.combine(day, dtime(hour, minute), tzinfo=place)
        at = local.astimezone(timezone.utc)
        if at > after:
            return at.replace(microsecond=0)
    raise AutomationError("That schedule never runs.")


def stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime(_STAMP)


def parse_stamp(text: str) -> datetime:
    return datetime.strptime(text, _STAMP).replace(tzinfo=timezone.utc)


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


# ------------------------------------------------------------- automations
def get(conn: sqlite3.Connection, automation_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM automations WHERE id = ?", (automation_id,)).fetchone()


def owned(conn: sqlite3.Connection, user_id: int, automation_id: int) -> sqlite3.Row:
    row = get(conn, automation_id)
    if row is None or row["owner_id"] != user_id:
        raise AutomationError("No such automation.")
    return row


def find(conn: sqlite3.Connection, user_id: int, name_or_id) -> sqlite3.Row:
    """One of this person's automations by id or name (case-insensitive)."""
    text = str(name_or_id or "").strip()
    row = None
    if text.isdigit():
        row = get(conn, int(text))
        row = row if row is not None and row["owner_id"] == user_id else None
    if row is None:
        row = conn.execute("SELECT * FROM automations WHERE owner_id = ? AND name = ? COLLATE NOCASE",
                           (user_id, text)).fetchone()
    if row is None:
        raise AutomationError(f"You have no automation called “{text}”; list_automations shows them.")
    return row


def for_user(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM automations WHERE owner_id = ? ORDER BY name COLLATE NOCASE",
                        (user_id,)).fetchall()


def all_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT a.*, u.username FROM automations a JOIN users u ON u.id = a.owner_id "
                        "ORDER BY u.username, a.name COLLATE NOCASE").fetchall()


def _deliver(conn: sqlite3.Connection, settings: Settings, user: sqlite3.Row, directory, runner: str,
             deliver: dict) -> dict:
    from mnemerynne import rooms

    if not isinstance(deliver, dict):
        raise AutomationError("Say where the answer goes.")
    out: dict = {"inbox": bool(deliver.get("inbox", False))}
    room_id = deliver.get("room_id")
    if room_id:
        # Erynne posting in a room reads only what everyone there may. Claude works with the owner's own access, so
        # its report may hold what only they can see: posting it shares that with the room, which they must say
        # they mean (deliver.room_share_ack, Doll's choice 2026-11-18). The post then says whose access it used.
        if runner != "erynne":
            if not deliver.get("room_share_ack") and not deliver.get("room_share"):
                raise AutomationError("Claude works with your own access, so its report can hold what only you may "
                                      "see. Tick “Share what my access finds with everyone in this room” to post it "
                                      "there, or pick Erynne, who reads only what the room may.")
            out["room_share"] = True
        with rooms.on(conn) as rooms_conn:
            if rooms_conn is None or not rooms.enabled(rooms_conn):
                raise AutomationError("Rooms is turned off.")
            try:
                r = rooms.room(rooms_conn, int(room_id))
                rooms._writable(rooms_conn, user, r, directory)
            except (rooms.RoomError, TypeError, ValueError):
                raise AutomationError("You can't post in that room.") from None
        if not r["erynne"]:
            raise AutomationError("Erynne is turned off in that room.")
        out["room_id"] = r["id"]
    if deliver.get("email_draft"):
        out["email_draft"] = True
    wanted = [int(d) for d in (deliver.get("destinations") or []) if str(d).isdigit()]
    if wanted:
        rows = {d["id"]: d for d in destinations_for(conn, user, directory)}
        if any(d not in rows for d in wanted):
            raise AutomationError("You can't send to one of those webhooks.")
        out["destinations"] = sorted(set(wanted))
    if not (out["inbox"] or out.get("room_id") or out.get("email_draft") or out.get("destinations")):
        raise AutomationError("Send the answer somewhere: your Inbox, a room, an email draft or a webhook.")
    return out


def check(conn: sqlite3.Connection, settings: Settings, user: sqlite3.Row, directory, body: dict,
          current: sqlite3.Row | None = None) -> dict:
    """The columns for a new automation, or the changed ones of `current`; raises AutomationError."""
    def given(key):
        return key in body and body[key] is not None

    out: dict = {}
    has_file = current is not None and "prompt_file_id" in current.keys()          # the column (automations.db 0005)
    if given("prompt_file_id"):                       # the instructions come from a file (0 / "" = the typed prompt)
        raw = body["prompt_file_id"]
        if raw in (0, "0", "", False):
            out["prompt_file_id"] = None                                  # back to the "What to do" box
        else:
            try:
                file_id = int(raw)
            except (TypeError, ValueError):
                raise AutomationError("Pick the prompt file from the list.") from None
            load_prompt_file(conn, settings, user["id"], file_id)         # readable by them, text, has instructions
            out["prompt_file_id"] = file_id
    file_id = out["prompt_file_id"] if "prompt_file_id" in out else (current["prompt_file_id"] if has_file else None)
    if "prompt_file_id" in out and out["prompt_file_id"] is None and not has_file:
        del out["prompt_file_id"]                     # "no file" on a new one: nothing to write (and core lacks it)
    if given("name") or current is None:
        name = str(body.get("name") or "").strip()
        if not name or len(name) > 80:
            raise AutomationError("Give it a name (up to 80 characters).")
        clash = conn.execute("SELECT id FROM automations WHERE owner_id = ? AND name = ? COLLATE NOCASE",
                             (user["id"], name)).fetchone()
        if clash and (current is None or clash["id"] != current["id"]):
            raise AutomationError(f"You already have an automation called “{name}”.")
        out["name"] = name
    if given("prompt") or current is None:
        prompt = str(body.get("prompt") or "").strip()
        if len(prompt) > MAX_PROMPT:
            raise AutomationError(f"Say what to do (up to {MAX_PROMPT} characters).")
        out["prompt"] = prompt                        # kept even with a prompt file: switching back loses nothing
    if not file_id and not out.get("prompt", current["prompt"] if current is not None else ""):
        raise AutomationError(f"Say what to do (up to {MAX_PROMPT} characters), or pick a prompt file.")
    if given("tz") or current is None:
        tz = str(body.get("tz") or settings.timezone or "UTC").strip()
        zone(tz)
        out["tz"] = tz
    if given("schedule") or current is None:
        out["schedule"] = json.dumps(check_schedule(body.get("schedule")))
    runner = str(body.get("runner") or (current["runner"] if current else "erynne"))
    if runner not in RUNNERS:
        raise AutomationError("The runner is Erynne, Claude with your key, or a Claude routine.")
    if given("runner") or current is None:
        out["runner"] = runner
    if given("tools"):
        tools = body["tools"]
        if not isinstance(tools, list) or len(tools) > 200 or not all(isinstance(t, str) and len(t) < 120 for t in tools):
            raise AutomationError("Tools are a list of tool names.")
        out["tools"] = json.dumps(sorted(set(tools)))
    if given("may_propose"):
        out["may_propose"] = int(bool(body["may_propose"]))
    if given("auto_approve"):
        out["auto_approve"] = int(bool(body["auto_approve"]))
    may_propose = out.get("may_propose", current["may_propose"] if current is not None else 0)
    if out.get("auto_approve") and not may_propose:   # nothing to approve unless it may propose
        raise AutomationError("Auto-approval needs “May propose changes” on.")
    if not may_propose and current is not None and "auto_approve" in current.keys() and current["auto_approve"]:
        out["auto_approve"] = 0                       # proposing switched off: auto-approval goes with it
    if given("deliver") or given("runner") or current is None:
        deliver = body.get("deliver") if given("deliver") else (json.loads(current["deliver"]) if current else {"inbox": True})
        out["deliver"] = json.dumps(_deliver(conn, settings, user, directory, runner, deliver))
    if given("routine_url"):
        url = str(body["routine_url"]).strip()
        if url and not ROUTINE_URL.match(url):
            raise AutomationError("A routine's address looks like https://api.anthropic.com/v1/claude_code/"
                                  "routines/trig_…/fire (claude.ai/code/routines → the routine → API trigger).")
        out["routine_url"] = url
    if given("routine_token"):
        token = str(body["routine_token"]).strip()
        if token and (not token.startswith("sk-ant-") or len(token) > 400 or " " in token):
            raise AutomationError("That isn't a routine token (they start sk-ant-).")
        out["routine_token_enc"] = apis.encrypt_secret(settings, token) if token else None
    if runner == "claude_routine":
        url = out.get("routine_url", current["routine_url"] if current else "")
        token = out["routine_token_enc"] if "routine_token_enc" in out else (current["routine_token_enc"] if current else None)
        if not url or not token:
            raise AutomationError("A Claude routine needs its API trigger's address and token.")
    if given("enabled"):
        out["enabled"] = int(bool(body["enabled"]))
        if out["enabled"]:
            out["paused_reason"] = ""
    return out


def create(conn: sqlite3.Connection, settings: Settings, user: sqlite3.Row, directory, body: dict) -> int:
    if conn.execute("SELECT COUNT(*) FROM automations WHERE owner_id = ?", (user["id"],)).fetchone()[0] >= MAX_PER_PERSON:
        raise AutomationError(f"You have {MAX_PER_PERSON} automations already; delete one first.")
    cols = check(conn, settings, user, directory, body)
    cols.setdefault("enabled", 1)
    cols["owner_id"] = user["id"]
    cols["next_run_at"] = stamp(next_run(json.loads(cols["schedule"]), cols["tz"], now())) if cols["enabled"] else None
    names = ", ".join(cols)
    return conn.execute(f"INSERT INTO automations({names}) VALUES ({', '.join('?' * len(cols))})",
                        tuple(cols.values())).lastrowid


def update(conn: sqlite3.Connection, settings: Settings, user: sqlite3.Row, directory, automation_id: int,
           body: dict) -> None:
    row = owned(conn, user["id"], automation_id)
    cols = check(conn, settings, user, directory, body, row)
    if not cols:
        return
    on = cols.get("enabled", row["enabled"])
    if "schedule" in cols or "tz" in cols or "enabled" in cols:
        schedule = json.loads(cols.get("schedule", row["schedule"]))
        cols["next_run_at"] = stamp(next_run(schedule, cols.get("tz", row["tz"]), now())) if on else None
    sets = ", ".join(f"{k} = ?" for k in cols)
    conn.execute(f"UPDATE automations SET {sets}, updated_at = datetime('now') WHERE id = ?",
                 (*cols.values(), automation_id))


def delete(conn: sqlite3.Connection, user_id: int, automation_id: int) -> None:
    owned(conn, user_id, automation_id)
    conn.execute("DELETE FROM automations WHERE id = ?", (automation_id,))


def pause(conn: sqlite3.Connection, automation_id: int, reason: str) -> None:
    """Switch it off and tell its owner (their Inbox, in core: once this change commits, so a rollback tells
    nobody)."""
    row = get(conn, automation_id)
    if row is None or not row["enabled"]:
        return
    conn.execute("UPDATE automations SET enabled = 0, next_run_at = NULL, paused_reason = ?, "
                 "updated_at = datetime('now') WHERE id = ?", (reason, automation_id))
    tell(conn, row["owner_id"], f"Your automation “{row['name']}” was paused: {reason}", f"/automations#a={automation_id}")


def tell(conn: sqlite3.Connection, user_id: int, text: str, link: str) -> None:
    """A note in someone's Inbox (core's user_messages) about an automation, once the transaction open on `conn`
    commits (now, if none is): `conn` may be automations.db's, which can't write core."""
    from mnemerynne import messages

    def send() -> None:
        with db.core_of(conn) as core:
            messages.system(core, user_id, text, link=link)
    db.after_commit(conn, send)


@contextmanager
def on(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A connection that can write Automator's tables: automations.db once the web app's start moved them there
    (docs/plans/services-rewrite.md, Phase 9), else core; `conn` itself when it's that one."""
    with db.table_connection(conn, "automations", "automations") as found:
        yield found if found is not None else conn


def due(conn: sqlite3.Connection, at: datetime) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM automations WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ? "
                        "ORDER BY next_run_at LIMIT 50", (stamp(at),)).fetchall()


def claim(conn: sqlite3.Connection, row: sqlite3.Row, at: datetime) -> bool:
    """Move a due automation to its next time; True if this caller should run it now (another scheduler
    hasn't, and it wasn't missed by more than MISSED_GRACE)."""
    schedule = json.loads(row["schedule"])
    following = next_run(schedule, row["tz"], at)
    won = conn.execute("UPDATE automations SET next_run_at = ? WHERE id = ? AND next_run_at = ?",
                       (stamp(following), row["id"], row["next_run_at"])).rowcount == 1
    return won and at - parse_stamp(row["next_run_at"]) <= MISSED_GRACE


def view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    schedule = json.loads(row["schedule"])
    last = conn.execute("SELECT id, status, started_at, finished_at FROM automation_runs WHERE id = ?",
                        (row["last_run_id"],)).fetchone() if row["last_run_id"] else None
    return {"id": row["id"], "name": row["name"], "prompt": row["prompt"], "schedule": schedule, "tz": row["tz"],
            "when": describe(schedule, row["tz"]), "runner": row["runner"], "tools": json.loads(row["tools"]),
            "may_propose": bool(row["may_propose"]), "deliver": json.loads(row["deliver"]),
            "auto_approve": bool(row["auto_approve"]) if "auto_approve" in row.keys() else False,   # its proposals applied at once
            "routine_url": row["routine_url"], "routine_token": "saved" if row["routine_token_enc"] else "",
            "enabled": bool(row["enabled"]), "paused_reason": row["paused_reason"],
            "next_run_at": row["next_run_at"] + " UTC" if row["next_run_at"] else None,
            "last_run": dict(last) if last else None,
            "running": bool(last and last["status"] in ACTIVE),         # the page offers Cancel while it's going
            "prompt_file": prompt_file_view(conn, row)}                  # None: the typed prompt is used


# ------------------------------------------------------------ prompt files
def prompt_from_text(text: str) -> str:
    """The instructions in a prompt file's text: its first fenced code block when it has one (so the file can
    explain itself around the block, like the IT-OPS prompt files), else the whole text."""
    text = text.lstrip("﻿")                                         # a byte-order mark from Notepad
    block = _FENCE.search(text)
    return (block.group(2) if block else text).strip()


def load_prompt_file(conn: sqlite3.Connection, settings: Settings, user_id: int, file_id: int) -> tuple[str, str, str]:
    """Read a prompt file as its automation's owner: (its name, its version's first 8 characters, the
    instructions). Raises AutomationError when it's gone, they can't read it, it isn't .md/.txt, or it's empty.
    `conn` reads core's files (core's own connection, or a feature's with core attached)."""
    from mnemerynne import editing

    try:
        loaded = editing.load(conn, settings, user_id, file_id, via="erynne")   # the owner's own access
    except editing.EditError:
        raise AutomationError(f"The prompt file (file {file_id}) is gone, or you can't read it any more.") from None
    name = loaded.row["display_name"]
    if loaded.fmt != "text":
        raise AutomationError(f"“{name}” can't be a prompt file: use a .md or .txt file.")
    text = prompt_from_text(loaded.data.decode("utf-8", errors="replace"))
    if not text:
        raise AutomationError(f"“{name}” is empty, so there's nothing to do.")
    if len(text) > MAX_PROMPT_FILE:
        raise AutomationError(f"“{name}” holds {len(text):,} characters of instructions; the most is {MAX_PROMPT_FILE:,}.")
    return name, loaded.version[:8], text


def prompt_for_run(settings: Settings, row: sqlite3.Row) -> tuple[str, str]:
    """What a run is asked to do, and a line for its raw output saying where that came from ("" = the typed
    prompt). A prompt file is read fresh each run, from core; AutomationError when it can't be."""
    file_id = row["prompt_file_id"] if "prompt_file_id" in row.keys() else None
    if not file_id:
        return row["prompt"], ""
    core = db.connect(settings.db_path)
    try:
        name, version, text = load_prompt_file(core, settings, row["owner_id"], file_id)
    finally:
        core.close()
    return text, f"Prompt: file {file_id} “{name}”, version {version}"


def prompt_file_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict | None:
    """The prompt file for the page and Claude: its id, name and link; missing when it's been deleted since."""
    file_id = row["prompt_file_id"] if "prompt_file_id" in row.keys() else None
    if not file_id:
        return None
    found = conn.execute("SELECT display_name, deleted_at FROM files WHERE id = ?", (file_id,)).fetchone()
    if found is None or found["deleted_at"] is not None:
        return {"id": file_id, "name": None, "missing": True}
    return {"id": file_id, "name": found["display_name"], "link": f"/files/{file_id}/view", "missing": False}


# -------------------------------------------------------------------- runs
def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Whether a table has a column yet: automations.db's later migrations add some that core's copy never had."""
    return any(c["name"] == column for c in conn.execute(f"PRAGMA table_info({table})"))


def start_run(conn: sqlite3.Connection, row: sqlite3.Row, trigger: str, status: str = "running",
              scheduled_for: str | None = None) -> int:
    """A new run: 'running' for the scheduler that runs it, 'queued' for one started in another process.
    scheduled_for: the time the scheduler claimed it for (a stamp), so the Timeline measures from then."""
    if scheduled_for and has_column(conn, "automation_runs", "scheduled_for"):
        run_id = conn.execute("INSERT INTO automation_runs(automation_id, trigger, status, scheduled_for) "
                              "VALUES (?, ?, ?, ?)", (row["id"], trigger, status, scheduled_for)).lastrowid
    else:
        run_id = conn.execute("INSERT INTO automation_runs(automation_id, trigger, status) VALUES (?, ?, ?)",
                              (row["id"], trigger, status)).lastrowid
    conn.execute("UPDATE automations SET last_run_id = ? WHERE id = ?", (run_id, row["id"]))
    return run_id


ACTIVE = ("queued", "running", "waiting", "reported")              # a run in one of these may still change
FINAL = ("done", "quiet", "failed", "timed_out", "cancelled")    # a run in one of these won't change any more


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, *, output: str = "", sources: list | None = None,
               error: str = "", model: str = "") -> bool:
    """Record how a run ended; False when it was cancelled first (a cancelled run stays cancelled: a late answer or
    failure doesn't overwrite it)."""
    return conn.execute("UPDATE automation_runs SET status = ?, output = ?, sources = ?, error = ?, model = ?, "
                        "finished_at = datetime('now') WHERE id = ? AND status <> 'cancelled'",
                        (status, output, json.dumps(sources or []), error[:2000], model or "", run_id)).rowcount == 1


def cancel_run(conn: sqlite3.Connection, run_id: int, who: str) -> bool:
    """Mark a run cancelled, if it's still going (ACTIVE); False when it had already ended (nothing changes then).
    Only the row: automations_run.cancel also stops whatever is running it."""
    return conn.execute("UPDATE automation_runs SET status = 'cancelled', finished_at = datetime('now'), error = ? "
                        f"WHERE id = ? AND status IN ({', '.join('?' * len(ACTIVE))})",
                        (f"Cancelled by {who}."[:2000], run_id, *ACTIVE)).rowcount == 1   # one UPDATE: no race


def get_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT r.*, a.owner_id, a.name FROM automation_runs r JOIN automations a "
                        "ON a.id = r.automation_id WHERE r.id = ?", (run_id,)).fetchone()


def active_run(conn: sqlite3.Connection, automation_id: int) -> sqlite3.Row | None:
    """The automation's run that's still going (queued, running, waiting or reported), if any: there's at most one."""
    return conn.execute(f"SELECT * FROM automation_runs WHERE automation_id = ? AND status IN "
                        f"({', '.join('?' * len(ACTIVE))}) ORDER BY id DESC LIMIT 1",
                        (automation_id, *ACTIVE)).fetchone()


def runs(conn: sqlite3.Connection, automation_id: int, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM automation_runs WHERE automation_id = ? ORDER BY id DESC LIMIT ?",
                        (automation_id, limit)).fetchall()


def run_view(row: sqlite3.Row, *, full: bool = True, trace: bool = False) -> dict:
    """A run for the page or Claude. `trace`: with its raw output too (the page's Raw output; it can be large, so
    Claude's get_automation_run leaves it out)."""
    out = {"run_id": row["id"], "automation_id": row["automation_id"], "trigger": row["trigger"],
           "status": row["status"], "started_at": row["started_at"] + " UTC",
           "finished_at": row["finished_at"] + " UTC" if row["finished_at"] else None,
           "error": row["error"], "model": row["model"], "claude_session_url": row["claude_session_url"],
           "delivered": json.loads(row["delivered"])}
    out["proposals"] = json.loads((row["proposals"] if "proposals" in row.keys() else "") or "[]")   # agent_proposals ids
    if full:
        out.update({"output": row["output"], "sources": json.loads(row["sources"])})
    if full and trace:   # the raw output, step by step (automations_run.Trace); none where the runs are still core's
        out["trace"] = json.loads((row["trace"] if "trace" in row.keys() else "") or "[]")
    return out


def expire_waiting(conn: sqlite3.Connection, at: datetime) -> int:
    """Routine runs whose report never came: timed out."""
    return conn.execute("UPDATE automation_runs SET status = 'timed_out', finished_at = datetime('now'), "
                        "error = 'The Claude routine didn''t report back in time.' "
                        "WHERE status = 'waiting' AND started_at < ?", (stamp(at - ROUTINE_WAIT),)).rowcount


# ---------------------------------------------------------------- timeline
TIMELINE_SAMPLE = 20                     # finished runs per automation its average is taken over
TIMELINE_MAX_SLOTS = 300                 # planned slots per automation in one window (every 15 min for 3 days)


def iso(at: datetime) -> str:
    """UTC as ISO 8601 with a Z, which every browser's Date reads."""
    return at.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_span(run: sqlite3.Row) -> tuple[datetime, datetime | None]:
    """When a run began and ended for the Timeline: from when it was scheduled (else when it started) until its
    last message went out (else when it finished; None while it's still going)."""
    keys = run.keys()
    begin = (run["scheduled_for"] if "scheduled_for" in keys else None) or run["started_at"]
    end = (run["delivered_at"] if "delivered_at" in keys else None) or run["finished_at"]
    return parse_stamp(begin), (parse_stamp(end) if end and run["status"] not in ACTIVE else None)


def run_stats(conn: sqlite3.Connection, automation_id: int) -> dict:
    """How long it takes: average and longest over its last TIMELINE_SAMPLE runs that ended well (done, quiet)."""
    rows = conn.execute("SELECT * FROM automation_runs WHERE automation_id = ? AND status IN ('done', 'quiet') "
                        "AND finished_at IS NOT NULL ORDER BY id DESC LIMIT ?", (automation_id, TIMELINE_SAMPLE)).fetchall()
    spans = [max(0.0, (end - begin).total_seconds()) for begin, end in map(run_span, rows) if end is not None]
    if not spans:
        return {"avg_seconds": None, "max_seconds": None, "measured": 0}
    return {"avg_seconds": round(sum(spans) / len(spans), 1), "max_seconds": round(max(spans), 1), "measured": len(spans)}


def planned(row: sqlite3.Row, start: datetime, end: datetime) -> list[datetime]:
    """Its scheduled times in [start, end), from its next run on (nothing while it's paused)."""
    if not row["enabled"] or not row["next_run_at"]:
        return []
    schedule = json.loads(row["schedule"])
    at, out = parse_stamp(row["next_run_at"]), []
    while at < end and len(out) < TIMELINE_MAX_SLOTS:
        if at >= start:
            out.append(at)
        at = next_run(schedule, row["tz"], at)                # strictly after: the following time
    return out


def timeline(conn: sqlite3.Connection, user_id: int, start: datetime, end: datetime) -> dict:
    """The Timeline tab: per automation, how long it takes (run_stats), its runs in the window (what really
    happened, and any still going, wherever it began), and its planned times there. Times are ISO UTC."""
    at_now = now()
    lanes = []
    for row in for_user(conn, user_id):
        stats = run_stats(conn, row["id"])
        runs = []
        for run in conn.execute(
                "SELECT * FROM automation_runs WHERE automation_id = ? AND (started_at >= ? OR status IN "
                f"({', '.join('?' * len(ACTIVE))})) ORDER BY id DESC LIMIT 400",
                (row["id"], stamp(start - timedelta(days=1)), *ACTIVE)):   # a day early: a slow run spanning midnight
            begin, finish = run_span(run)
            going = run["status"] in ACTIVE
            if (finish or at_now) < start or begin >= end:
                continue                                         # outside the window
            runs.append({"run_id": run["id"], "status": run["status"], "trigger": run["trigger"],
                         "start": iso(begin), "end": iso(finish) if finish else None, "running": going,
                         "seconds": round(((finish or at_now) - begin).total_seconds(), 1)})
        lanes.append({"id": row["id"], "name": row["name"], "runner": row["runner"], "enabled": bool(row["enabled"]),
                      "paused_reason": row["paused_reason"], "schedule": json.loads(row["schedule"]), "tz": row["tz"],
                      "when": describe(json.loads(row["schedule"]), row["tz"]), **stats,
                      "next_run_at": iso(parse_stamp(row["next_run_at"])) if row["next_run_at"] else None,
                      "runs": runs[::-1], "planned": [iso(t) for t in planned(row, max(start, at_now), end)]})
    return {"start": iso(start), "end": iso(end), "now": iso(at_now), "sample": TIMELINE_SAMPLE, "automations": lanes}


# -------------------------------------------------------- admin's choices
def destinations_for(conn: sqlite3.Connection, user: sqlite3.Row, directory) -> list[sqlite3.Row]:
    rows = conn.execute("SELECT * FROM automation_destinations ORDER BY name COLLATE NOCASE").fetchall()
    return [d for d in rows if not d["group_ref"] or group_refs.in_any(conn, user, directory, d["group_ref"])]


def owner(conn: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row | None:
    return users.get_user_by_id(conn, row["owner_id"])
