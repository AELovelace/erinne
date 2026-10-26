"""Announcements: a notice an admin posts (Access → Announcements) for everyone or for a group,
shown as a card on every page (notify.js) until the person presses "Got it", or, when the
announcement must be acknowledged, "I acknowledge". The Markdown body is rendered like the
file view (raw HTML escaped); an optional link points at a page in Mnemerynne. Expired ones
stop showing at once and are deleted by the worker after announcement_days.
"""

from __future__ import annotations

import re
import sqlite3

from mnemerynne import group_refs, messages
from mnemerynne.ingest import markdown

MAX_TITLE = 120
MAX_BODY = 4000
_WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$")


class NoticeError(ValueError):
    """Refused; the message is safe to show."""


def _check(title: str | None, body: str | None, link: str | None, expires_at: str | None) -> tuple[str, str, str, str | None]:
    title = (title or "").strip()
    if not title or len(title) > MAX_TITLE:
        raise NoticeError(f"Give the announcement a title (up to {MAX_TITLE} characters).")
    body = (body or "").strip()
    if len(body) > MAX_BODY:
        raise NoticeError(f"Keep the text under {MAX_BODY} characters.")
    try:
        link = messages.safe_link(link)
    except messages.MessageError as exc:
        raise NoticeError(str(exc)) from None
    expires_at = (expires_at or "").strip() or None
    if expires_at is not None:
        if not _WHEN.match(expires_at):
            raise NoticeError("Expiry is a date like 2026-11-30, or 2026-11-30 17:00 (UTC).")
        if len(expires_at) == 10:
            expires_at += " 23:59:59"
    return title, body, link, expires_at


def create(conn: sqlite3.Connection, admin_id: int, title: str, body: str = "", *, link: str = "",
           group_ref: str = "", must_ack: bool = False, expires_at: str | None = None) -> int:
    title, body, link, expires_at = _check(title, body, link, expires_at)
    return conn.execute(
        "INSERT INTO announcements(title, body, link, group_ref, must_ack, expires_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", (title, body, link, group_ref or "", int(bool(must_ack)), expires_at, admin_id)
    ).lastrowid


def get(conn: sqlite3.Connection, announcement_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM announcements WHERE id = ?", (announcement_id,)).fetchone()
    if row is None:
        raise NoticeError("No such announcement.")
    return row


def update(conn: sqlite3.Connection, announcement_id: int, **fields) -> None:
    """Change some fields (title, body, link, group_ref, must_ack, expires_at)."""
    row = get(conn, announcement_id)
    title, body, link, expires_at = _check(fields.get("title", row["title"]), fields.get("body", row["body"]),
                                           fields.get("link", row["link"]),
                                           fields["expires_at"] if "expires_at" in fields else row["expires_at"])
    conn.execute("UPDATE announcements SET title = ?, body = ?, link = ?, group_ref = ?, must_ack = ?, expires_at = ?, "
                 "updated_at = datetime('now') WHERE id = ?",
                 (title, body, link, fields.get("group_ref", row["group_ref"]) or "",
                  int(bool(fields.get("must_ack", row["must_ack"]))), expires_at, announcement_id))


def delete(conn: sqlite3.Connection, announcement_id: int) -> bool:
    return conn.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,)).rowcount > 0


def listing(conn: sqlite3.Connection) -> list[dict]:
    """Every announcement for the admin page, newest first, with how many saw and acknowledged it."""
    rows = conn.execute(
        "SELECT a.*, u.username AS author, "
        "(SELECT count(*) FROM announcement_marks m WHERE m.announcement_id = a.id AND m.seen_at IS NOT NULL) AS seen, "
        "(SELECT count(*) FROM announcement_marks m WHERE m.announcement_id = a.id AND m.acked_at IS NOT NULL) AS acked, "
        "(a.expires_at IS NOT NULL AND a.expires_at <= datetime('now')) AS expired "
        "FROM announcements a LEFT JOIN users u ON u.id = a.created_by ORDER BY a.id DESC").fetchall()
    return [{"id": r["id"], "title": r["title"], "body": r["body"], "link": r["link"], "group_ref": r["group_ref"],
             "group": group_refs.label(conn, r["group_ref"]), "must_ack": bool(r["must_ack"]),
             "expires_at": r["expires_at"], "expired": bool(r["expired"]), "author": r["author"],
             "created_at": r["created_at"], "seen": r["seen"], "acked": r["acked"]} for r in rows]


def active_for(conn: sqlite3.Connection, user: sqlite3.Row, directory, groups: set[str] | None = None) -> list[dict]:
    """The announcements this person should see now (not expired, for everyone or their group), with
    whether they saw and acknowledged each; newest first."""
    rows = conn.execute(
        "SELECT a.*, m.seen_at, m.acked_at FROM announcements a "
        "LEFT JOIN announcement_marks m ON m.announcement_id = a.id AND m.user_id = ? "
        "WHERE a.expires_at IS NULL OR a.expires_at > datetime('now') ORDER BY a.id DESC", (user["id"],)).fetchall()
    out = []
    for r in rows:
        if r["group_ref"]:
            if groups is None:
                groups = group_refs.member_of(conn, user, directory)
            if not group_refs.in_any(conn, user, directory, r["group_ref"], groups):
                continue
        out.append({"id": r["id"], "title": r["title"], "html": markdown.render_html(r["body"]) if r["body"] else "",
                    "link": r["link"], "must_ack": bool(r["must_ack"]), "seen": r["seen_at"] is not None,
                    "acked": r["acked_at"] is not None, "created_at": r["created_at"] + " UTC",
                    "expires_at": r["expires_at"]})
    return out


def pending_for(conn: sqlite3.Connection, user: sqlite3.Row, directory, groups: set[str] | None = None) -> list[dict]:
    """The ones to show as cards: not seen yet, or waiting for an acknowledgement."""
    return [a for a in active_for(conn, user, directory, groups) if not a["seen"] or (a["must_ack"] and not a["acked"])]


def mark(conn: sqlite3.Connection, user: sqlite3.Row, directory, announcement_id: int, ack: bool) -> None:
    """The person has seen it (or acknowledged it); only for announcements meant for them."""
    found = next((a for a in active_for(conn, user, directory) if a["id"] == announcement_id), None)
    if found is None:
        raise NoticeError("No such announcement.")
    if found["must_ack"] and not ack:
        raise NoticeError("This one has to be acknowledged.")
    conn.execute(
        "INSERT INTO announcement_marks(user_id, announcement_id, seen_at, acked_at) VALUES (?, ?, datetime('now'), ?) "
        "ON CONFLICT(user_id, announcement_id) DO UPDATE SET seen_at = COALESCE(seen_at, excluded.seen_at), "
        "acked_at = COALESCE(acked_at, excluded.acked_at)",
        (user["id"], announcement_id, "now" if ack else None))
    if ack:
        conn.execute("UPDATE announcement_marks SET acked_at = datetime('now') WHERE user_id = ? AND announcement_id = ? "
                     "AND acked_at = 'now'", (user["id"], announcement_id))
