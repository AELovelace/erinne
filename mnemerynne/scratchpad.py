"""The agent scratchpad: notes a person's own agents leave for each other.

Someone running several Claude Code sessions (or Claude Desktop and claude.ai) against the same
Mnemerynne needs those sessions to hand work over: "steps 1-3 done, step 4 next, leave the backup
job alone". The scratchpad is that notebook. Notes sit on boards named after the work
("nas-migration"); every connection of the person shares them, and nobody else sees them.

A note can be claimed by one agent so two sessions don't take the same task: another agent asking
for it is refused with Conflict naming the holder, and a claim older than the claim timeout lapses
by itself (release_stale runs before every read and claim). Resolving a note marks it done; writing
with `replaces` marks the older note superseded so the board shows one current state.

Boards are implicit (distinct `board` values); there is no board table. Notes untouched for the
keep period are purged by retention.purge whatever their status, into scratchpad_archive (as are boards
cleared and notes deleted), where they can still be searched.

The person writes on boards too, from the Scratchpad page (web/scratchpad.py): a note with author 'person'
is a direction, for one agent label or everyone on the board. directions_for hands each session the
directions meant for it once, with its next connector call (mcp_server's call()); sessions are seen through
touch_session, which every connector call makes.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime

from mnemerynne import db

MAX_BOARD = 60              # characters in a board key
MAX_BODY = 4000             # characters in one note
MAX_AGENT = 40              # characters in an agent label
MAX_OPEN = 500              # open notes one person may have (a looping agent stops here)
MAX_LISTED = 200            # notes one read returns at most
_BOARD = re.compile(r"^[a-z0-9][a-z0-9._-]{0,59}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class ScratchpadError(ValueError):
    """Refused; safe to show. status: the HTTP status the web layer answers with."""
    status = 400


class NotFound(ScratchpadError):
    status = 404


class Conflict(ScratchpadError):
    status = 409


# ------------------------------------------------------------------ inputs
def board_key(text) -> str:
    """A board's key from what the agent typed: lower-case, spaces to dashes, [a-z0-9._-], 1-60 chars."""
    key = re.sub(r"\s+", "-", str(text or "").strip().lower())   # "NAS migration" → "nas-migration"
    if not _BOARD.match(key):
        raise ScratchpadError(f"A board name is 1-{MAX_BOARD} characters: letters, digits, dots, dashes or "
                              f"underscores, e.g. nas-migration.")
    return key


def agent_label(text) -> str:
    """An agent's label as it signs notes: one line, trimmed, at most MAX_AGENT characters."""
    label = _CONTROL.sub(" ", str(text or "")).strip()
    label = re.sub(r"\s+", " ", label)[:MAX_AGENT].strip()
    if not label:
        raise ScratchpadError("Give the agent a short label, such as the machine or task it's on.")
    return label


def since_at(text) -> str | None:
    """`since` as SQLite compares it ('YYYY-MM-DD HH:MM:SS', UTC): accepts a date, an ISO date-time,
    a trailing Z or ' UTC', or what an earlier read returned. None when nothing was given."""
    raw = str(text or "").strip()
    if not raw:
        return None
    cleaned = raw.replace(" UTC", "").replace("T", " ").rstrip("Z").strip()
    cleaned = re.sub(r"[+-]\d\d:\d\d$", "", cleaned)                   # drop an offset: notes are stored in UTC
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise ScratchpadError("`since` is a UTC date or date-time, e.g. 2026-10-28 or 2026-10-28T09:00.")


# ------------------------------------------------------------------- rows
def _row(r: sqlite3.Row) -> dict:
    """A note as the tools and the briefing show it."""
    out = {"note_id": r["id"], "board": r["board"], "agent": r["agent"], "status": r["status"],
           "at": r["created_at"] + " UTC", "updated": r["updated_at"] + " UTC", "text": r["body"]}
    if r["author"] == "person":
        out["author"] = "person"          # the person's own direction, not another agent's note
        out["for"] = r["for_agent"] or "everyone on the board"
    elif r["for_agent"]:
        out["for"] = r["for_agent"]
    if r["reply_to"]:
        out["reply_to"] = r["reply_to"]
    if r["urgent"]:
        out["urgent"] = True
    if r["claimed_by"]:
        out["claimed_by"] = r["claimed_by"]
        out["claimed_at"] = r["claimed_at"] + " UTC"
    if r["done_at"]:
        out["done_at"] = r["done_at"] + " UTC"
    return out


def _get(conn: sqlite3.Connection, user_id: int, note_id) -> sqlite3.Row:
    """One of the person's notes, or NotFound (another person's note looks the same as none)."""
    try:
        note_id = int(note_id)
    except (TypeError, ValueError):
        raise ScratchpadError("note_id is a number from read_scratchpad.") from None
    row = conn.execute("SELECT * FROM scratchpad_notes WHERE id = ? AND user_id = ?", (note_id, user_id)).fetchone()
    if row is None:
        raise NotFound(f"There is no note {note_id} on your scratchpad.")
    return row


def _minutes_since(conn: sqlite3.Connection, stamp: str) -> int:
    """Whole minutes between a stored UTC stamp and now, by SQLite's clock (the one that wrote it)."""
    return int(conn.execute("SELECT (julianday('now') - julianday(?)) * 1440", (stamp,)).fetchone()[0])


# ------------------------------------------------------------------ claims
def release_stale(conn: sqlite3.Connection, user_id: int, minutes: int) -> int:
    """Drop claims older than `minutes` (a session that stopped without resolving). Returns how many."""
    if minutes <= 0:
        return 0
    return conn.execute(
        "UPDATE scratchpad_notes SET claimed_by = NULL, claimed_at = NULL, updated_at = datetime('now') "
        "WHERE user_id = ? AND claimed_by IS NOT NULL AND claimed_at < datetime('now', ?)",
        (user_id, f"-{int(minutes)} minutes")).rowcount


def claim(conn: sqlite3.Connection, user_id: int, note_id, agent: str, *, release: bool = False,
          minutes: int) -> dict:
    """Take a note for `agent`, or hand it back with release=True. Taking a note another agent holds is
    refused with Conflict; taking one you already hold just refreshes the claim."""
    with db.transaction(conn):                                     # BEGIN IMMEDIATE: two sessions can't both win
        release_stale(conn, user_id, minutes)
        row = _get(conn, user_id, note_id)
        holder = row["claimed_by"]
        if release:
            if holder and holder != agent:
                raise Conflict(f"Note {row['id']} is claimed by {holder}, not by {agent}; only they can release it.")
            conn.execute("UPDATE scratchpad_notes SET claimed_by = NULL, claimed_at = NULL, "
                         "updated_at = datetime('now') WHERE id = ?", (row["id"],))
        else:
            if row["status"] != "open":
                raise ScratchpadError(f"Note {row['id']} is {row['status']}; only open notes can be claimed "
                                      f"(resolve_note with done=false reopens one).")
            if holder and holder != agent:
                age = _minutes_since(conn, row["claimed_at"])
                raise Conflict(f"Note {row['id']} is claimed by {holder} ({age} min ago). Pick another task, or "
                               f"wait: a claim lapses after {minutes} minutes unless the note is resolved.")
            conn.execute("UPDATE scratchpad_notes SET claimed_by = ?, claimed_at = datetime('now'), "
                         "updated_at = datetime('now') WHERE id = ?", (agent, row["id"]))
    return _row(_get(conn, user_id, note_id))


# ------------------------------------------------------------------- notes
def boards(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """The person's boards, most recently touched first, with what's on each."""
    rows = conn.execute(
        "SELECT board, SUM(status = 'open') AS open, SUM(status = 'open' AND claimed_by IS NOT NULL) AS claimed, "
        "SUM(status = 'done') AS done, MAX(updated_at) AS last FROM scratchpad_notes WHERE user_id = ? "
        "GROUP BY board ORDER BY last DESC", (user_id,)).fetchall()
    return [{"board": r["board"], "open": r["open"], "claimed": r["claimed"], "done": r["done"],
             "last": r["last"] + " UTC"} for r in rows]


def notes(conn: sqlite3.Connection, user_id: int, board: str, *, since=None, include_done: bool = False,
          limit: int = 50, minutes: int = 0) -> list[dict]:
    """A board's notes, oldest first: open ones, or everything with include_done (done and superseded
    too); `since` keeps those changed at or after that UTC time. Stale claims are released first."""
    key = board_key(board)
    stamp = since_at(since)
    release_stale(conn, user_id, minutes)
    where, params = ["user_id = ?", "board = ?"], [user_id, key]
    if not include_done:
        where.append("status = 'open'")
    if stamp:
        where.append("updated_at >= ?")
        params.append(stamp)
    params.append(max(1, min(int(limit or 50), MAX_LISTED)))
    rows = conn.execute(f"SELECT * FROM scratchpad_notes WHERE {' AND '.join(where)} ORDER BY id LIMIT ?",
                        params).fetchall()
    return [_row(r) for r in rows]


def write(conn: sqlite3.Connection, user_id: int, board: str, body: str, agent: str, connection: str = "", *,
          replaces=None, author: str = "agent", for_agent: str | None = None, reply_to=None,
          urgent: bool = False) -> int:
    """Jot a note on a board. `replaces` (one of the person's own note ids on the same board) marks that
    note superseded, so the board carries one current state instead of a trail of corrections.
    author 'person' (the Scratchpad page only) makes it a direction; `for_agent` addresses one agent label;
    `reply_to` answers a note on the same board."""
    key = board_key(board)
    if author not in ("agent", "person"):
        raise ScratchpadError("A note is written by an agent or by the person.")
    for_agent = agent_label(for_agent) if for_agent and str(for_agent).strip() else None
    if reply_to is not None:
        answered = _get(conn, user_id, reply_to)
        if answered["board"] != key:
            raise ScratchpadError(f"Note {answered['id']} is on board {answered['board']}, not {key}; reply there.")
    body = str(body or "").strip()
    if not body:
        raise ScratchpadError("Write something for the note.")
    if len(body) > MAX_BODY:
        raise ScratchpadError(f"Keep a note under {MAX_BODY} characters; put long material in a file and link it.")
    open_count = conn.execute("SELECT count(*) FROM scratchpad_notes WHERE user_id = ? AND status = 'open'",
                              (user_id,)).fetchone()[0]
    if open_count >= MAX_OPEN:
        raise ScratchpadError(f"The scratchpad already holds {MAX_OPEN} open notes; resolve some before adding more.")
    with db.transaction(conn):
        if replaces is not None:
            old = _get(conn, user_id, replaces)
            if old["board"] != key:
                raise ScratchpadError(f"Note {old['id']} is on board {old['board']}, not {key}.")
            conn.execute("UPDATE scratchpad_notes SET status = 'superseded', claimed_by = NULL, claimed_at = NULL, "
                         "updated_at = datetime('now') WHERE id = ? AND status <> 'superseded'", (old["id"],))
        cur = conn.execute("INSERT INTO scratchpad_notes(user_id, board, agent, connection, body, author, for_agent, "
                           "reply_to, urgent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (user_id, key, agent, connection or "", body, author, for_agent,
                            int(reply_to) if reply_to is not None else None, int(bool(urgent))))
    return cur.lastrowid


def resolve(conn: sqlite3.Connection, user_id: int, note_id, done: bool = True) -> dict:
    """Mark a note done (clearing any claim), or open again with done=False."""
    row = _get(conn, user_id, note_id)
    if row["status"] == "superseded":
        raise ScratchpadError(f"Note {row['id']} was replaced by a newer note; resolve that one instead.")
    if done:
        conn.execute("UPDATE scratchpad_notes SET status = 'done', done_at = datetime('now'), claimed_by = NULL, "
                     "claimed_at = NULL, updated_at = datetime('now') WHERE id = ?", (row["id"],))
    else:
        conn.execute("UPDATE scratchpad_notes SET status = 'open', done_at = NULL, updated_at = datetime('now') "
                     "WHERE id = ?", (row["id"],))
    return _row(_get(conn, user_id, note_id))


# ----------------------------------------------------------------- archive
_ARCHIVED = ("id, user_id, board, agent, connection, body, status, author, for_agent, reply_to, urgent, claimed_by, "
             "created_at, updated_at, done_at")


def _archive(conn: sqlite3.Connection, where: str, params: tuple, reason: str) -> int:
    """Move notes into scratchpad_archive (then off the scratchpad). Returns how many. The caller's transaction,
    or one of its own."""
    def move() -> int:
        conn.execute(f"INSERT OR REPLACE INTO scratchpad_archive({_ARCHIVED}, reason) "
                     f"SELECT {_ARCHIVED}, ? FROM scratchpad_notes WHERE {where}", (reason, *params))
        return conn.execute(f"DELETE FROM scratchpad_notes WHERE {where}", params).rowcount
    if conn.in_transaction:
        return move()
    with db.transaction(conn):
        return move()


def clear(conn: sqlite3.Connection, user_id: int, board: str | None = None) -> int:
    """Take the person's notes off the scratchpad: one board's, or all of them. They stay in the archive.
    Returns how many."""
    if board:
        return _archive(conn, "user_id = ? AND board = ?", (user_id, board_key(board)), "cleared")
    return _archive(conn, "user_id = ?", (user_id,), "cleared")


def delete(conn: sqlite3.Connection, user_id: int, note_id) -> None:
    """Take one note off the scratchpad (into the archive)."""
    row = _get(conn, user_id, note_id)
    _archive(conn, "id = ?", (row["id"],), "deleted")


def expire(conn: sqlite3.Connection, days: int) -> int:
    """Notes nobody touched in `days` (everyone's), into the archive (retention.purge, hourly)."""
    if days <= 0:
        return 0
    return _archive(conn, "updated_at < datetime('now', ?)", (f"-{int(days)} days",), "expired")


def search_archive(conn: sqlite3.Connection, user_id: int, query: str = "", board: str | None = None,
                   limit: int = 50) -> list[dict]:
    """The person's archived notes, newest first: text, agent or board containing `query`, on `board`."""
    where, params = ["user_id = ?"], [user_id]
    if board:
        where.append("board = ?")
        params.append(board_key(board))
    words = [w for w in str(query or "").split() if w][:8]
    for w in words:
        where.append("(body LIKE ? ESCAPE '\\' OR agent LIKE ? ESCAPE '\\' OR board LIKE ? ESCAPE '\\')")
        like = "%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params += [like, like, like]
    params.append(max(1, min(int(limit or 50), MAX_LISTED)))
    rows = conn.execute(f"SELECT * FROM scratchpad_archive WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
                        params).fetchall()
    return [{**_row(r), "archived": r["archived_at"] + " UTC", "reason": r["reason"]} for r in rows]


# ---------------------------------------------------------------- sessions
SESSION_ACTIVE_MINUTES = 10
SESSION_GONE_HOURS = 24


def touch_session(conn: sqlite3.Connection, user_id: int, connection: str, agent: str, tool: str,
                  board: str | None = None) -> None:
    """A connector call from this session: when, which tool, and (scratchpad tools) on which board."""
    now = "datetime('now')"
    conn.execute(f"UPDATE scratchpad_sessions SET last_at = {now}, last_tool = ? WHERE user_id = ? AND connection = ? "
                 "AND agent = ?", (tool, user_id, connection, agent))
    conn.execute("INSERT INTO scratchpad_sessions(user_id, connection, agent, board, last_tool) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(user_id, connection, agent, board) DO UPDATE SET last_at = datetime('now'), "
                 "last_tool = excluded.last_tool", (user_id, connection, agent, board_key(board) if board else "", tool))


def session_label(conn: sqlite3.Connection, user_id: int, connection: str) -> str | None:
    """The label this connection signed with most recently (on any board), or None."""
    row = conn.execute("SELECT agent FROM scratchpad_sessions WHERE user_id = ? AND connection = ? "
                       "ORDER BY last_at DESC, rowid DESC LIMIT 1", (user_id, connection)).fetchone()
    return row["agent"] if row else None


def sessions(conn: sqlite3.Connection, user_id: int, board: str | None = None) -> list[dict]:
    """The person's sessions seen in the last week (on `board`, or anywhere): label, state, last tool, boards,
    and the notes each holds a claim on."""
    rows = conn.execute("SELECT * FROM scratchpad_sessions WHERE user_id = ? AND last_at > datetime('now', '-7 days') "
                        "ORDER BY last_at DESC", (user_id,)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        s = out.setdefault(r["agent"].lower(), {"agent": r["agent"], "boards": [], "last_at": r["last_at"] + " UTC",
                                                "last_tool": r["last_tool"], "_last": r["last_at"]})
        if r["board"] and r["board"] not in s["boards"]:
            s["boards"].append(r["board"])
    if board:
        key = board_key(board)
        out = {k: v for k, v in out.items() if key in v["boards"]}
    claims = conn.execute("SELECT id, claimed_by FROM scratchpad_notes WHERE user_id = ? AND status = 'open' "
                          "AND claimed_by IS NOT NULL", (user_id,)).fetchall()
    for s in out.values():
        age = _minutes_since(conn, s.pop("_last"))
        s["state"] = ("active" if age <= SESSION_ACTIVE_MINUTES else
                      "idle" if age <= SESSION_GONE_HOURS * 60 else "gone")
        s["minutes_ago"] = age
        s["holds"] = [c["id"] for c in claims if c["claimed_by"].lower() == s["agent"].lower()]
    return list(out.values())


# -------------------------------------------------------------- directions
MAX_DIRECTIONS = 5


def _for_this(r, agent: str, boards: set[str]) -> bool:
    if r["for_agent"]:
        return r["for_agent"].lower() == agent.lower()
    return r["board"] in boards


def open_directions(conn: sqlite3.Connection, user_id: int, agent: str, *, board: str | None = None) -> list[dict]:
    """The person's directions still open for this agent: addressed to it, or to everyone on a board it has
    worked on (or `board`), oldest first."""
    boards = {r[0] for r in conn.execute("SELECT DISTINCT board FROM scratchpad_sessions WHERE user_id = ? "
                                         "AND lower(agent) = lower(?) AND board != ''", (user_id, agent))}
    if board:
        boards.add(board_key(board))
    rows = conn.execute("SELECT * FROM scratchpad_notes WHERE user_id = ? AND author = 'person' AND status = 'open' "
                        "ORDER BY urgent DESC, id", (user_id,)).fetchall()
    return [_row(r) for r in rows if _for_this(r, agent, boards)]


def directions_for(conn: sqlite3.Connection, user_id: int, agent: str) -> list[dict]:
    """Directions this agent hasn't been shown yet (each once), newest urgent first; marks them seen."""
    fresh = [d for d in open_directions(conn, user_id, agent) if not conn.execute(
        "SELECT 1 FROM scratchpad_seen WHERE note_id = ? AND agent = ?", (d["note_id"], agent.lower())).fetchone()]
    fresh = fresh[:MAX_DIRECTIONS]
    for d in fresh:
        conn.execute("INSERT OR IGNORE INTO scratchpad_seen(note_id, agent) VALUES (?, ?)", (d["note_id"], agent.lower()))
    return fresh


def seen_by(conn: sqlite3.Connection, note_ids: list[int]) -> dict[int, list[dict]]:
    """For the page: which agents were shown each direction, and when."""
    if not note_ids:
        return {}
    marks = ",".join("?" * len(note_ids))
    out: dict[int, list[dict]] = {}
    for r in conn.execute(f"SELECT note_id, agent, seen_at FROM scratchpad_seen WHERE note_id IN ({marks}) "
                          "ORDER BY seen_at", note_ids):
        out.setdefault(r["note_id"], []).append({"agent": r["agent"], "at": r["seen_at"] + " UTC"})
    return out


# -------------------------------------------------------------------- page
def feed(conn: sqlite3.Connection, user_id: int, board: str | None, after: str | None = None,
         minutes: int = 0) -> dict:
    """What the Scratchpad page needs, changed since `after` (a cursor from the last answer): the boards, the
    board's notes (every status), who saw its directions, its sessions, and notes taken off since then."""
    release_stale(conn, user_id, minutes)
    cursor = conn.execute("SELECT datetime('now')").fetchone()[0]
    stamp = since_at(after)
    out: dict = {"cursor": cursor, "boards": boards(conn, user_id)}
    waiting = dict(conn.execute("SELECT board, count(*) FROM scratchpad_notes WHERE user_id = ? AND author = 'person' "
                                "AND status = 'open' GROUP BY board", (user_id,)).fetchall())
    for b in out["boards"]:
        b["directions"] = waiting.get(b["board"], 0)
    if board:
        key = board_key(board)
        where, params = "user_id = ? AND board = ?", [user_id, key]
        if stamp:
            where += " AND updated_at >= ?"
            params.append(stamp)
        rows = conn.execute(f"SELECT * FROM scratchpad_notes WHERE {where} ORDER BY id LIMIT 500", params).fetchall()
        seen = seen_by(conn, [r["id"] for r in rows if r["author"] == "person"])
        out["board"] = key
        out["notes"] = [{**_row(r), "seen_by": seen.get(r["id"], []), "claimed_by": r["claimed_by"]} for r in rows]
        out["sessions"] = sessions(conn, user_id, key)
        out["gone"] = [r[0] for r in conn.execute(
            "SELECT id FROM scratchpad_archive WHERE user_id = ? AND board = ? AND archived_at >= ?",
            (user_id, key, stamp))] if stamp else []
    else:
        out["sessions"] = sessions(conn, user_id)
    return out


def summary(conn: sqlite3.Connection, user_id: int, minutes: int = 0) -> dict:
    """For the briefing and Preferences: open notes per board, and the claims live right now."""
    release_stale(conn, user_id, minutes)
    listed = [b for b in boards(conn, user_id) if b["open"]]
    claims = conn.execute(
        "SELECT id, board, claimed_by, claimed_at FROM scratchpad_notes WHERE user_id = ? AND status = 'open' "
        "AND claimed_by IS NOT NULL ORDER BY claimed_at", (user_id,)).fetchall()
    return {"open": sum(b["open"] for b in listed), "boards": listed,
            "claims": [{"note_id": c["id"], "board": c["board"], "claimed_by": c["claimed_by"],
                        "since": c["claimed_at"] + " UTC"} for c in claims]}
