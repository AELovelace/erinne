"""What changed in a library (or a folder in it) since a given time, file by file: added, changed, binned
and commented on, and by whom, so Claude can notice another agent's reply without browsing and guessing.

Built from what files already record (created_at, updated_at, uploaded_by, edited_via, deleted_at), the
versions kept for AI edits (file_versions) and comments (file_comments); only files the caller can read.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from mnemerynne import comments, fs

MAX_FILES = 100


def parse_since(text: str | None, default_hours: int = 24) -> str:
    """A UTC time as the database writes it ("YYYY-MM-DD HH:MM:SS"), from "2026-10-24", "2026-10-24 02:00"
    or ISO with T and Z or an offset; nothing means the last default_hours."""
    if not (text or "").strip():
        moment = datetime.now(timezone.utc) - timedelta(hours=default_hours)
    else:
        try:
            moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Give `since` as a UTC date or time, e.g. 2026-10-24 or 2026-10-24 14:30.") from None
        if moment.tzinfo is not None:
            moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _seconds_apart(a: str, b: str) -> float:
    return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds())


def changes(conn: sqlite3.Connection, access: fs.Access, collection_id: int, folder_id: int | None,
            since: str) -> list[dict]:
    """Files in the place (and below) with anything at or after `since` (parse_since), the most recently
    active first."""
    scope = fs.scope_folders(conn, access, collection_id, folder_id)
    live = fs.live_folder_ids(conn, collection_id)
    rows = conn.execute(
        "SELECT f.*, u.username AS uploader, d.username AS binner, "
        " (SELECT count(*) FROM file_versions v WHERE v.file_id = f.id) AS versions, "
        " (SELECT count(*) FROM file_versions v WHERE v.file_id = f.id AND v.created_at >= :since) AS edits, "
        " (SELECT count(*) FROM file_comments c WHERE c.file_id = f.id AND c.created_at >= :since) AS new_comments "
        "FROM files f LEFT JOIN users u ON u.id = f.uploaded_by LEFT JOIN users d ON d.id = f.deleted_by "
        "WHERE f.collection_id = :cid AND (f.created_at >= :since OR f.updated_at >= :since "
        " OR f.deleted_at >= :since OR EXISTS (SELECT 1 FROM file_comments c WHERE c.file_id = f.id "
        " AND c.created_at >= :since))",
        {"cid": collection_id, "since": since}).fetchall()
    out = []
    for r in rows:
        folder = r["folder_id"]
        if folder is not None and folder not in live:
            continue                                  # inside a binned folder
        if scope is not None and folder not in scope:
            continue
        if (access.claude and r["claude_hidden"]) or not access.can_read(collection_id, folder):
            continue
        if r["deleted_at"] is not None and r["deleted_at"] < since:
            continue                                  # binned before the window
        entry: dict = {"file_id": r["id"], "file": r["display_name"],
                       "folder": "/".join(p["name"] for p in fs.folder_path(conn, folder)) or "/"}
        moments = []
        if r["created_at"] >= since:
            # On a file never edited since, edited_via is who made it (create_file sets it).
            entry["added"] = {"at": r["created_at"] + " UTC",
                              "by": comments.who(r["uploader"], None if r["versions"] else r["edited_via"])}
            moments.append(r["created_at"])
        if r["updated_at"] >= since and r["updated_at"] > r["created_at"]:
            last = conn.execute("SELECT v.created_at, u.username FROM file_versions v LEFT JOIN users u "
                                "ON u.id = v.created_by WHERE v.file_id = ? ORDER BY v.id DESC LIMIT 1",
                                (r["id"],)).fetchone()
            # An AI edit keeps the old version in the same step; any other change is someone in Files.
            by_ai = last is not None and _seconds_apart(last["created_at"], r["updated_at"]) <= 2
            entry["changed"] = {"at": r["updated_at"] + " UTC",
                                "by": (comments.who(last["username"], r["edited_via"]) if by_ai
                                       else "someone in Files (an upload, move or rename)"),
                                **({"ai_edits": r["edits"]} if r["edits"] else {})}
            moments.append(r["updated_at"])
        if r["new_comments"]:
            last = conn.execute("SELECT c.created_at, c.via, u.username FROM file_comments c LEFT JOIN users u "
                                "ON u.id = c.user_id WHERE c.file_id = ? ORDER BY c.id DESC LIMIT 1",
                                (r["id"],)).fetchone()
            entry["comments"] = {"new": r["new_comments"], "last_at": last["created_at"] + " UTC",
                                 "last_by": comments.who(last["username"],
                                                         "claude" if last["via"] == "claude" else None)}
            moments.append(last["created_at"])
        if r["deleted_at"] is not None:
            entry["binned"] = {"at": r["deleted_at"] + " UTC", "by": comments.who(r["binner"])}
            moments.append(r["deleted_at"])
        if moments:
            entry["last_activity"] = max(moments) + " UTC"
            out.append(entry)
    out.sort(key=lambda e: e["last_activity"], reverse=True)
    return out[:MAX_FILES]
