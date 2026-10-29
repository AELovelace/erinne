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
keep period are purged by retention.purge whatever their status.
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
          replaces=None) -> int:
    """Jot a note on a board. `replaces` (one of the person's own note ids on the same board) marks that
    note superseded, so the board carries one current state instead of a trail of corrections."""
    key = board_key(board)
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
        cur = conn.execute("INSERT INTO scratchpad_notes(user_id, board, agent, connection, body) VALUES (?, ?, ?, ?, ?)",
                           (user_id, key, agent, connection or "", body))
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


def clear(conn: sqlite3.Connection, user_id: int, board: str | None = None) -> int:
    """Delete the person's notes: one board's, or all of them (the Preferences button). Returns how many."""
    if board:
        return conn.execute("DELETE FROM scratchpad_notes WHERE user_id = ? AND board = ?",
                            (user_id, board_key(board))).rowcount
    return conn.execute("DELETE FROM scratchpad_notes WHERE user_id = ?", (user_id,)).rowcount


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
