"""Automator: jobs a person schedules (a morning briefing, a security digest, an API health check).

An automation is a prompt, a schedule and a runner:
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
from datetime import datetime, time as dtime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mnemerynne import accounts, apis, group_refs, users
from mnemerynne.config import Settings

ENABLED_KEY = "automations_enabled"
GROUP_KEY = "automations_group"
RUNNERS = ("erynne", "claude_key", "claude_routine")
TRIGGERS = ("schedule", "manual", "mcp")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_PER_PERSON = 20
MIN_MINUTES = 15
MAX_PROMPT = 8000
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
    {"every": "minutes" | "hours", "n": N}                 (at least MIN_MINUTES apart)."""
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
        return {"every": "minutes", "n": minutes}
    raise AutomationError("A schedule runs every day at a time, or every so many minutes or hours.")


def describe(schedule: dict, tz: str) -> str:
    if schedule["every"] == "day":
        days = schedule["days"]
        when = ("every day" if len(days) == 7 else "weekdays" if days == list(DAYS[:5])
                else "weekends" if days == ["sat", "sun"] else ", ".join(d.capitalize() for d in days))
        return f"{when} at {schedule['at']} ({tz})"
    n = schedule["n"]
    return f"every {n // 60} hour{'s' if n // 60 != 1 else ''}" if n % 60 == 0 else f"every {n} minutes"


def next_run(schedule: dict, tz: str, after: datetime) -> datetime:
    """The first time strictly after `after` (aware, any zone) that the schedule runs, in UTC."""
    after = after.astimezone(timezone.utc)
    if schedule["every"] == "minutes":
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
        if runner != "erynne":
            raise AutomationError("Only Erynne posts in rooms: everyone there reads it, so she reads only what "
                                  "they all may. Pick Erynne, or send Claude's answer to your Inbox.")
        if not rooms.enabled(conn):
            raise AutomationError("Rooms is turned off.")
        try:
            r = rooms.room(conn, int(room_id))
            rooms._writable(conn, user, r, directory)
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
        if not prompt or len(prompt) > MAX_PROMPT:
            raise AutomationError(f"Say what to do (up to {MAX_PROMPT} characters).")
        out["prompt"] = prompt
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
    from mnemerynne import messages

    row = get(conn, automation_id)
    if row is None or not row["enabled"]:
        return
    conn.execute("UPDATE automations SET enabled = 0, next_run_at = NULL, paused_reason = ?, "
                 "updated_at = datetime('now') WHERE id = ?", (reason, automation_id))
    messages.system(conn, row["owner_id"], f"Your automation “{row['name']}” was paused: {reason}",
                    link=f"/automations#a={automation_id}")


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
            "routine_url": row["routine_url"], "routine_token": "saved" if row["routine_token_enc"] else "",
            "enabled": bool(row["enabled"]), "paused_reason": row["paused_reason"],
            "next_run_at": row["next_run_at"] + " UTC" if row["next_run_at"] else None,
            "last_run": dict(last) if last else None}


# -------------------------------------------------------------------- runs
def start_run(conn: sqlite3.Connection, row: sqlite3.Row, trigger: str) -> int:
    run_id = conn.execute("INSERT INTO automation_runs(automation_id, trigger) VALUES (?, ?)",
                          (row["id"], trigger)).lastrowid
    conn.execute("UPDATE automations SET last_run_id = ? WHERE id = ?", (run_id, row["id"]))
    return run_id


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, *, output: str = "", sources: list | None = None,
               error: str = "", model: str = "") -> None:
    conn.execute("UPDATE automation_runs SET status = ?, output = ?, sources = ?, error = ?, model = ?, "
                 "finished_at = datetime('now') WHERE id = ?",
                 (status, output, json.dumps(sources or []), error[:2000], model or "", run_id))


def get_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT r.*, a.owner_id, a.name FROM automation_runs r JOIN automations a "
                        "ON a.id = r.automation_id WHERE r.id = ?", (run_id,)).fetchone()


def runs(conn: sqlite3.Connection, automation_id: int, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM automation_runs WHERE automation_id = ? ORDER BY id DESC LIMIT ?",
                        (automation_id, limit)).fetchall()


def run_view(row: sqlite3.Row, *, full: bool = True) -> dict:
    out = {"run_id": row["id"], "automation_id": row["automation_id"], "trigger": row["trigger"],
           "status": row["status"], "started_at": row["started_at"] + " UTC",
           "finished_at": row["finished_at"] + " UTC" if row["finished_at"] else None,
           "error": row["error"], "model": row["model"], "claude_session_url": row["claude_session_url"],
           "delivered": json.loads(row["delivered"])}
    if full:
        out.update({"output": row["output"], "sources": json.loads(row["sources"])})
    return out


def expire_waiting(conn: sqlite3.Connection, at: datetime) -> int:
    """Routine runs whose report never came: timed out."""
    return conn.execute("UPDATE automation_runs SET status = 'timed_out', finished_at = datetime('now'), "
                        "error = 'The Claude routine didn''t report back in time.' "
                        "WHERE status = 'waiting' AND started_at < ?", (stamp(at - ROUTINE_WAIT),)).rowcount


# -------------------------------------------------------- admin's choices
def destinations_for(conn: sqlite3.Connection, user: sqlite3.Row, directory) -> list[sqlite3.Row]:
    rows = conn.execute("SELECT * FROM automation_destinations ORDER BY name COLLATE NOCASE").fetchall()
    return [d for d in rows if not d["group_ref"] or group_refs.in_any(conn, user, directory, d["group_ref"])]


def owner(conn: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row | None:
    return users.get_user_by_id(conn, row["owner_id"])
