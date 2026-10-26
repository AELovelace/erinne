"""Messages inside Mnemerynne: short notes between people, and what Mnemerynne tells them.

A note is one person writing to another by username, in the browser (Messages page), or through
their Claude (send_message) or Erynne in chat; the sender is shown as "alice", "Claude for alice"
or "Erynne for alice". A system message (kind 'system', no sender) is Mnemerynne telling someone
what happened: an admin decided their proposal, someone commented on their file, their account
changed. Both land in the same inbox, count towards the unread badge, and are kept message_days.

Messages are plain text, at most MAX_CHARS, with one optional link to a place in Mnemerynne
(a path, never another site), so a note can point at a file, a page or a proposal.
"""

from __future__ import annotations

import sqlite3
from urllib.parse import urlsplit

from mnemerynne import comments, users

MAX_CHARS = 2000
MAX_LINK = 500
MAX_LISTED = 100
MAX_PER_HOUR = 60          # notes one sender may send in an hour (a looping agent stops here)


class MessageError(ValueError):
    """Refused; the message is safe to show."""


def safe_link(link: str | None, public_url: str = "") -> str:
    """A link to a place in Mnemerynne: a path starting with '/', or this server's own absolute
    address (Claude hands back the links tools gave it), reduced to the path. Anything else is refused."""
    link = (link or "").strip()
    if not link:
        return ""
    base = public_url.rstrip("/")
    if base and link.lower().startswith(base.lower() + "/"):
        link = link[len(base):]
    parts = urlsplit(link)
    if (len(link) > MAX_LINK or not link.startswith("/") or link.startswith("//") or "\\" in link
            or parts.scheme or parts.netloc or any(ord(c) < 32 or c == "\x7f" for c in link)):
        raise MessageError("A link has to be a page in Mnemerynne, like /files/12/view or /wiki/40.")
    return link


def who(username: str | None, via: str) -> str:
    """Who a message is from, as people see it."""
    if via == "system":
        return "Mnemerynne"
    return comments.who(username, via if via in ("claude", "erynne") else None)


def people(conn: sqlite3.Connection, except_user_id: int | None = None) -> list[str]:
    """Active usernames, for the "to" box."""
    return [r[0] for r in conn.execute("SELECT username FROM users WHERE active = 1 AND id IS NOT ? "
                                       "ORDER BY username COLLATE NOCASE", (except_user_id,))]


def send(conn: sqlite3.Connection, from_user: sqlite3.Row, to: str, body: str, *, via: str,
         link: str | None = None, public_url: str = "") -> int:
    """A note from one person to another (via: web, claude or erynne, for how they wrote it)."""
    target = users.get_user(conn, to or "")
    if target is None or not target["active"]:
        raise MessageError(f"There's no one called “{(to or '').strip()}” here.")
    if target["id"] == from_user["id"]:
        raise MessageError("That's you; pick someone else.")
    body = (body or "").strip()
    if not body:
        raise MessageError("Write something to send.")
    if len(body) > MAX_CHARS:
        raise MessageError(f"Keep a message under {MAX_CHARS} characters.")
    link = safe_link(link, public_url)
    recent = conn.execute("SELECT count(*) FROM user_messages WHERE from_user_id = ? "
                          "AND created_at > datetime('now', '-1 hour')", (from_user["id"],)).fetchone()[0]
    if recent >= MAX_PER_HOUR:
        raise MessageError("That's a lot of messages in one hour; wait a while before sending more.")
    return conn.execute(
        "INSERT INTO user_messages(from_user_id, to_user_id, kind, via, body, link) VALUES (?, ?, 'note', ?, ?, ?)",
        (from_user["id"], target["id"], via, body, link)).lastrowid


def system(conn: sqlite3.Connection, to_user_id: int, body: str, link: str = "") -> int | None:
    """Mnemerynne tells someone something. Never raises: a hook must not break what it reports on."""
    try:
        body = (body or "").strip()[:MAX_CHARS]
        if not body or conn.execute("SELECT 1 FROM users WHERE id = ?", (to_user_id,)).fetchone() is None:
            return None
        try:
            link = safe_link(link)
        except MessageError:
            link = ""
        return conn.execute(
            "INSERT INTO user_messages(from_user_id, to_user_id, kind, via, body, link) "
            "VALUES (NULL, ?, 'system', 'system', ?, ?)", (to_user_id, body, link)).lastrowid
    except sqlite3.Error:
        return None


def _row(r: sqlite3.Row) -> dict:
    out = {"message_id": r["id"], "kind": r["kind"], "from": who(r["from_name"], r["via"]),
           "from_user": r["from_name"], "at": r["created_at"] + " UTC", "text": r["body"], "link": r["link"],
           "read": r["read_at"] is not None}
    if "to_name" in r.keys():
        out["to"] = r["to_name"]
    return out


def inbox(conn: sqlite3.Connection, user_id: int, *, unread_only: bool = False, limit: int = MAX_LISTED) -> list[dict]:
    """Messages to a person, unread first, newest first."""
    rows = conn.execute(
        "SELECT m.*, f.username AS from_name FROM user_messages m LEFT JOIN users f ON f.id = m.from_user_id "
        f"WHERE m.to_user_id = ? {'AND m.read_at IS NULL' if unread_only else ''} "
        "ORDER BY m.read_at IS NOT NULL, m.id DESC LIMIT ?", (user_id, max(1, min(int(limit), MAX_LISTED)))).fetchall()
    return [_row(r) for r in rows]


def sent(conn: sqlite3.Connection, user_id: int, limit: int = MAX_LISTED) -> list[dict]:
    rows = conn.execute(
        "SELECT m.*, f.username AS from_name, t.username AS to_name FROM user_messages m "
        "LEFT JOIN users f ON f.id = m.from_user_id JOIN users t ON t.id = m.to_user_id "
        "WHERE m.from_user_id = ? ORDER BY m.id DESC LIMIT ?", (user_id, max(1, min(int(limit), MAX_LISTED)))).fetchall()
    return [_row(r) for r in rows]


def mark_read(conn: sqlite3.Connection, user_id: int, message_id: int) -> bool:
    """True when the message is theirs (read now, or already)."""
    if conn.execute("SELECT 1 FROM user_messages WHERE id = ? AND to_user_id = ?", (message_id, user_id)).fetchone() is None:
        return False
    conn.execute("UPDATE user_messages SET read_at = datetime('now') WHERE id = ? AND read_at IS NULL", (message_id,))
    return True


def mark_all_read(conn: sqlite3.Connection, user_id: int) -> int:
    return conn.execute("UPDATE user_messages SET read_at = datetime('now') WHERE to_user_id = ? AND read_at IS NULL",
                        (user_id,)).rowcount


def unread_count(conn: sqlite3.Connection, user_id: int) -> int:
    return conn.execute("SELECT count(*) FROM user_messages WHERE to_user_id = ? AND read_at IS NULL",
                        (user_id,)).fetchone()[0]
