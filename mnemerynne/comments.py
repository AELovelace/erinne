"""Comments on files: notes and replies people (in Files) and Claude (through the connector) leave on a
file, so they can talk about a document, or hand work to each other, without editing it.

Whoever can read a file can read and add its comments; Claude only on files it may see, and only with
a token that may edit (a search-only connection can't write anything). A comment goes with its file:
moving a file keeps them, deleting it for good removes them.
"""

from __future__ import annotations

import sqlite3

from mnemerynne import fs

MAX_CHARS = 4000
MAX_LISTED = 200


class CommentError(ValueError):
    """Refused; the message is safe to show."""


def who(username: str | None, via: str | None = None) -> str:
    """"alice", or "Claude for alice" for something Claude did as alice. via: "claude", or files.edited_via
    ("claude:alice", "erynne:alice"), whose name wins."""
    if not via:
        return username or "someone"
    assistant, _, name = via.partition(":")
    return f"{assistant.capitalize()} for {name or username or 'someone'}"


def readable_file(conn: sqlite3.Connection, access: fs.Access, file_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM files WHERE id = ? AND deleted_at IS NULL", (file_id,)).fetchone()
    if row is None or not fs.is_live_folder(conn, row["folder_id"]) or not access.can_read_file(row):
        raise CommentError("No such file" + (" (or it isn't shared with Claude)." if access.claude else "."))
    return row


def listing(conn: sqlite3.Connection, access: fs.Access, file_id: int) -> list[dict]:
    """A file's comments, oldest first; replies carry reply_to."""
    readable_file(conn, access, file_id)
    rows = conn.execute(
        "SELECT c.*, u.username FROM file_comments c LEFT JOIN users u ON u.id = c.user_id WHERE c.file_id = ? "
        "ORDER BY c.id DESC LIMIT ?", (file_id, MAX_LISTED)).fetchall()
    return [{"comment_id": r["id"], **({"reply_to": r["parent_id"]} if r["parent_id"] else {}),
             "by": who(r["username"], "claude" if r["via"] == "claude" else None),
             "at": r["created_at"] + " UTC", "text": r["body"]} for r in reversed(rows)]


def add(conn: sqlite3.Connection, access: fs.Access, file_id: int, body: str, *, via: str,
        reply_to: int | None = None) -> int:
    readable_file(conn, access, file_id)
    body = (body or "").strip()
    if not body:
        raise CommentError("Write something to comment.")
    if len(body) > MAX_CHARS:
        raise CommentError(f"Keep a comment under {MAX_CHARS} characters.")
    if reply_to is not None and conn.execute("SELECT 1 FROM file_comments WHERE id = ? AND file_id = ?",
                                             (reply_to, file_id)).fetchone() is None:
        raise CommentError("There's no such comment on this file to reply to.")
    row = conn.execute("SELECT uploaded_by, display_name FROM files WHERE id = ?", (file_id,)).fetchone()
    others = {r[0] for r in conn.execute("SELECT DISTINCT user_id FROM file_comments WHERE file_id = ? "
                                         "AND user_id IS NOT NULL", (file_id,))}
    comment_id = conn.execute("INSERT INTO file_comments(file_id, parent_id, user_id, via, body) VALUES (?, ?, ?, ?, ?)",
                              (file_id, reply_to, access.user_id, via, body)).lastrowid
    # Tell whoever has a stake in the file: its uploader and the earlier commenters, not the author.
    from mnemerynne import messages   # messages imports comments: lazy, to keep the modules apart

    author = conn.execute("SELECT username FROM users WHERE id = ?", (access.user_id,)).fetchone()
    by = who(author["username"] if author else None, "claude" if via == "claude" else None)
    for uid in (others | {row["uploaded_by"]}) - {access.user_id, None}:
        messages.system(conn, uid, f"{by} commented on “{row['display_name']}”: {body[:140]}",
                        link=f"/files/{file_id}/view")
    return comment_id


def counts(conn: sqlite3.Connection, file_ids: list[int]) -> dict[int, int]:
    if not file_ids:
        return {}
    marks = ",".join("?" * len(file_ids))
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT file_id, count(*) FROM file_comments WHERE file_id IN ({marks}) GROUP BY file_id", file_ids)}
