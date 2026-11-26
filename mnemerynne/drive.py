"""The desktop app's drive (clients/desktop-app): libraries as folders in File Explorer, files on demand.

The app lists a folder the first time Explorer opens it (listing), downloads a file's bytes when something opens
it, and sends changes back through web/desktop.py. To hear about changes made elsewhere it asks for the change
feed (changes) per library: every add, rename, move, new content, bin or restore of a file or folder takes the
next number from the `drive_seq` counter (migration 0078, kept by triggers), so "everything after N" never misses
anything. Each entry is the item's state now, not what happened, so applying one twice is harmless.

Everything goes through fs.Access, the same as the web pages: the drive never shows a person more than Files does.
SharePoint libraries are left out: their files live in SharePoint and open there.
"""

from __future__ import annotations

import sqlite3

from mnemerynne import db, fs, permissions

FEED_LIMIT = 1000        # entries per answer; the app asks again with the new cursor while `more` is true


def cursor_now(conn: sqlite3.Connection) -> int:
    """The newest change number: a library listed now is up to date as of this cursor."""
    row = conn.execute("SELECT seq FROM drive_seq WHERE id = 1").fetchone()
    return int(row["seq"]) if row else 0


def _sharepoint_ids(conn: sqlite3.Connection) -> set[int]:
    from mnemerynne import sharepoint                  # imported late, as fs.Access does (keeps start-up light)
    return sharepoint.sharepoint_ids(conn)             # libraries whose files live in SharePoint


def libraries(conn: sqlite3.Connection, access: fs.Access) -> list[dict]:
    """The libraries this person may put on the drive: their File box and every library they can read."""
    skip = _sharepoint_ids(conn)
    out = []
    for c in permissions.readable_collections(conn, access.user_id):
        if c["id"] in skip:
            continue
        out.append({"id": c["id"], "kind": c["kind"],
                    "name": permissions.PRIVATE_LABEL if c["kind"] == "private" else c["name"],
                    "can_write": access.can_write(c["id"], None)})
    return out


def _file_entry(row, sha: str, access: fs.Access) -> dict:
    """One file as the drive sees it. `modified` is the server's last change (UTC, 'YYYY-MM-DD HH:MM:SS')."""
    return {"type": "file", "id": row["id"], "folder_id": row["folder_id"], "name": row["display_name"],
            "size": row["size"], "sha256": sha, "modified": row["updated_at"] or row["created_at"],
            "can_write": access.can_write_file(row)}


def _shas(conn: sqlite3.Connection, ids: list[int]) -> dict[int, str]:
    """file id -> its content's SHA-256 (the version the app sends back in If-Match)."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {r["id"]: r["blob_sha256"] for r in conn.execute(f"SELECT id, blob_sha256 FROM files WHERE id IN ({marks})", ids)}


def listing(conn: sqlite3.Connection, access: fs.Access, collection_id: int, folder_id: int | None) -> dict:
    """One folder's subfolders and files (fs.browse, so the same as Files shows), plus the cursor to watch from."""
    if collection_id in _sharepoint_ids(conn):
        raise fs.NotFound("Not found")                 # SharePoint libraries aren't on the drive
    cursor = cursor_now(conn)                          # read first: anything after it shows up in the feed
    found = fs.browse(conn, access, collection_id, folder_id)
    shas = _shas(conn, [r["id"] for r in found["files"]])
    return {"folders": [{"type": "folder", "id": r["id"], "parent_id": folder_id, "name": r["name"]}
                        for r in found["folders"]],
            "files": [_file_entry(r, shas[r["id"]], access) for r in found["files"]],
            "can_write": found["can_write"], "cursor": cursor}


def changes(conn: sqlite3.Connection, access: fs.Access, collection_id: int, cursor: int,
            limit: int = FEED_LIMIT) -> dict:
    """What changed in a library after `cursor`, oldest first: each file and folder's state now.

    A file or folder that was binned, moved somewhere this person can't read, or sits in a binned folder comes
    back with "deleted": true. A binned folder takes everything below it, so the app removes the whole subtree.
    `cursor` is where to ask from next; `more` says there's another page waiting."""
    access.see(collection_id, None)                    # 404 for a library they can't open, like Files
    if collection_id in _sharepoint_ids(conn):
        raise fs.NotFound("Not found")
    with db.read_transaction(conn):                    # one snapshot: the rows and the counter agree
        files = conn.execute(
            f"SELECT {fs._FILE_COLUMNS}, f.blob_sha256, f.change_seq {fs._FILE_FROM} "
            "WHERE f.collection_id = ? AND f.change_seq > ? ORDER BY f.change_seq LIMIT ?",
            (collection_id, cursor, limit)).fetchall()
        folders = conn.execute(
            "SELECT id, parent_id, name, deleted_at, change_seq FROM folders WHERE collection_id = ? "
            "AND change_seq > ? ORDER BY change_seq LIMIT ?", (collection_id, cursor, limit)).fetchall()
        counter = cursor_now(conn)                     # every change in this library up to here is in the lists
    # Two lists cut at `limit` each: only what's below both cut points is complete, so stop at the lower one.
    cut = None
    if len(files) == limit:
        cut = files[-1]["change_seq"]
    if len(folders) == limit:
        cut = folders[-1]["change_seq"] if cut is None else min(cut, folders[-1]["change_seq"])
    live = fs.live_folder_ids(conn, collection_id)
    visible = access.visible(collection_id, [r["id"] for r in folders])
    entries: list[tuple[int, dict]] = []
    for r in folders:
        if cut is not None and r["change_seq"] > cut:
            continue
        gone = r["deleted_at"] is not None or r["id"] not in live or r["id"] not in visible
        entries.append((r["change_seq"], {"type": "folder", "id": r["id"], "parent_id": r["parent_id"],
                                          "name": r["name"], "deleted": gone}))
    access.prefetch([r for r in files if r["deleted_at"] is None])
    for r in files:
        if cut is not None and r["change_seq"] > cut:
            continue
        in_place = r["folder_id"] is None or r["folder_id"] in live
        gone = r["deleted_at"] is not None or not in_place or not access.can_read_file(r)
        entry = ({"type": "file", "id": r["id"], "folder_id": r["folder_id"], "name": r["display_name"],
                  "deleted": True} if gone else {**_file_entry(r, r["blob_sha256"], access), "deleted": False})
        entries.append((r["change_seq"], entry))
    entries.sort(key=lambda e: e[0])
    # Cut short: carry on from the cut. Complete: everything up to the counter has been seen, so start there
    # (a quiet library then doesn't rescan the same rows next time).
    next_cursor = cut if cut is not None else max(counter, cursor)
    return {"changes": [e for _, e in entries], "cursor": next_cursor, "more": cut is not None}
