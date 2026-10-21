"""The file manager: folders, rename, move, copy and the recycle bin.

Folders live inside a collection (a user's inbox or a library); NULL folder
means the collection root. Permissions stay per collection: anyone who can
write a collection can organise everything in it.

Search scope travels with each file's chunks and vectors (collection_id,
folder_id), so every operation that changes where a file lives, or whether
it is in the bin, calls `_sync_scope` in the same transaction. A file in the
bin, or under a folder in the bin, gets collection 0, which no search allows.

Only the item that was put in the bin gets `deleted_at`; its contents keep
their place and are hidden because an ancestor is in the bin, so restoring a
folder brings everything back as it was.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from erinne import jobs, permissions
from erinne.config import Settings
from erinne.db import transaction
from erinne.ingest import store

TRASH_COLLECTION = 0  # chunk/vector scope for anything in the bin


class FsError(ValueError):
    """An operation was refused; the message is safe to show to the user."""
    status = 400


class NotFound(FsError):
    status = 404


class Forbidden(FsError):
    status = 403


class Conflict(FsError):
    status = 409


@dataclass
class Access:
    """What one user may read and write, looked up once per request."""
    user_id: int
    readable: set[int]
    writable: set[int]

    @classmethod
    def of(cls, conn: sqlite3.Connection, user_id: int) -> "Access":
        return cls(user_id, permissions.readable_collection_ids(conn, user_id),
                   permissions.writable_collection_ids(conn, user_id))

    def read(self, collection_id: int) -> None:
        # Same 404 whether it doesn't exist or isn't yours: no existence leaks.
        if collection_id not in self.readable:
            raise NotFound("Not found")

    def write(self, collection_id: int) -> None:
        self.read(collection_id)
        if collection_id not in self.writable:
            raise Forbidden("You can't change files here")


@dataclass
class Item:
    type: str  # 'file' | 'folder'
    id: int


@dataclass
class Result:
    item: Item
    ok: bool = True
    error: str | None = None
    status: int = 200
    extra: dict = field(default_factory=dict)

    def json(self) -> dict:
        return {"type": self.item.type, "id": self.item.id, "ok": self.ok, "error": self.error,
                "status": self.status, **self.extra}


# --------------------------------------------------------------------- names
_COPY_RE = re.compile(r"^(.*?) \((\d+)\)$")


def clean_name(name: str) -> str:
    cleaned = store.clean_display_name(name)
    if cleaned in (".", "..") or not (name or "").strip():
        raise FsError("Please give it a name.")
    return cleaned


def _taken(conn: sqlite3.Connection, collection_id: int, folder_id: int | None, name: str, kind: str,
           exclude_id: int | None = None) -> bool:
    if kind == "folder":
        sql = ("SELECT 1 FROM folders WHERE collection_id = ? AND COALESCE(parent_id, 0) = ? "
               "AND lower(name) = lower(?) AND deleted_at IS NULL AND id IS NOT ?")
    else:
        sql = ("SELECT 1 FROM files WHERE collection_id = ? AND COALESCE(folder_id, 0) = ? "
               "AND lower(display_name) = lower(?) AND deleted_at IS NULL AND id IS NOT ?")
    return conn.execute(sql, (collection_id, folder_id or 0, name, exclude_id)).fetchone() is not None


def unique_name(conn: sqlite3.Connection, collection_id: int, folder_id: int | None, name: str, kind: str,
                exclude_id: int | None = None) -> str:
    """`name`, or "name (2).ext", "name (3).ext"… if it is taken in that folder."""
    if not _taken(conn, collection_id, folder_id, name, kind, exclude_id):
        return name
    path = PurePosixPath(name)
    stem, suffix = (path.stem, path.suffix) if kind == "file" else (name, "")
    match = _COPY_RE.match(stem)
    base, n = (match.group(1), int(match.group(2))) if match else (stem, 1)
    while True:
        n += 1
        candidate = f"{base} ({n}){suffix}"
        if not _taken(conn, collection_id, folder_id, candidate, kind, exclude_id):
            return candidate


# ------------------------------------------------------------------- folders
def _folder(conn: sqlite3.Connection, folder_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if row is None:
        raise NotFound("Folder not found")
    return row


def _folders_of(conn: sqlite3.Connection, collection_id: int) -> dict[int, sqlite3.Row]:
    return {r["id"]: r for r in conn.execute("SELECT * FROM folders WHERE collection_id = ?", (collection_id,))}


def live_folder_ids(conn: sqlite3.Connection, collection_id: int) -> set[int]:
    """Folders that are neither in the bin nor inside a folder in the bin."""
    folders = _folders_of(conn, collection_id)
    live: dict[int, bool] = {}

    def check(fid: int) -> bool:
        if fid not in live:
            row = folders[fid]
            live[fid] = row["deleted_at"] is None and (row["parent_id"] is None or check(row["parent_id"]))
        return live[fid]

    return {fid for fid in folders if check(fid)}


def is_live_folder(conn: sqlite3.Connection, folder_id: int | None) -> bool:
    if folder_id is None:
        return True
    row = conn.execute("SELECT collection_id FROM folders WHERE id = ?", (folder_id,)).fetchone()
    return row is not None and folder_id in live_folder_ids(conn, row["collection_id"])


def subtree(conn: sqlite3.Connection, folder_id: int) -> list[int]:
    """The folder and everything below it (in the bin or not)."""
    return [r[0] for r in conn.execute(
        "WITH RECURSIVE t(id) AS (SELECT ? UNION ALL SELECT f.id FROM folders f JOIN t ON f.parent_id = t.id) "
        "SELECT id FROM t", (folder_id,))]


def folder_path(conn: sqlite3.Connection, folder_id: int | None) -> list[dict]:
    """[{id, name}] from the collection root down to the folder."""
    path = []
    while folder_id is not None:
        row = _folder(conn, folder_id)
        path.append({"id": row["id"], "name": row["name"]})
        folder_id = row["parent_id"]
    return path[::-1]


def require_folder(conn: sqlite3.Connection, collection_id: int, folder_id: int | None) -> None:
    if folder_id is None:
        return
    row = conn.execute("SELECT collection_id FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if row is None or row["collection_id"] != collection_id or not is_live_folder(conn, folder_id):
        raise NotFound("Folder not found")


def create_folder(conn: sqlite3.Connection, access: Access, collection_id: int, parent_id: int | None,
                  name: str) -> int:
    access.write(collection_id)
    name = clean_name(name)
    with transaction(conn):
        require_folder(conn, collection_id, parent_id)
        if _taken(conn, collection_id, parent_id, name, "folder"):
            raise Conflict(f"There is already a folder called “{name}” here.")
        return conn.execute(
            "INSERT INTO folders(collection_id, parent_id, name, created_by) VALUES (?, ?, ?, ?)",
            (collection_id, parent_id, name, access.user_id)).lastrowid


def ensure_path(conn: sqlite3.Connection, collection_id: int, parent_id: int | None, parts: list[str],
                user_id: int | None) -> int | None:
    """The folder at parent/parts[0]/parts[1]…, creating what is missing.

    Runs inside the caller's transaction (or autocommit). Used by folder
    uploads and imports; the caller has checked write access.
    """
    folder_id = parent_id
    for part in parts:
        name = clean_name(part)
        row = conn.execute(
            "SELECT id FROM folders WHERE collection_id = ? AND COALESCE(parent_id, 0) = ? "
            "AND lower(name) = lower(?) AND deleted_at IS NULL", (collection_id, folder_id or 0, name)).fetchone()
        folder_id = row["id"] if row else conn.execute(
            "INSERT INTO folders(collection_id, parent_id, name, created_by) VALUES (?, ?, ?, ?)",
            (collection_id, folder_id, name, user_id)).lastrowid
    return folder_id


def split_relative_path(relative_path: str) -> list[str]:
    """Folder parts of a browser-supplied relative path ("a/b/file.pdf" -> [a, b]).

    Empty, "." and ".." parts are dropped: paths can only go down.
    """
    parts = [p for p in (relative_path or "").replace("\\", "/").split("/")]
    return [p for p in parts[:-1] if p.strip() and p.strip() not in (".", "..")][:32]


# ----------------------------------------------------------------- browsing
_FILE_COLUMNS = """
    f.id, f.collection_id, f.folder_id, f.display_name, f.status, f.progress, f.error, f.notes,
    f.created_at, f.updated_at, f.deleted_at, f.uploaded_by, b.kind, b.mime, b.size, b.unit_count,
    b.rel_path, b.ocr_rel_path, u.username AS uploaded_by_name
"""
_FILE_FROM = """
    FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 LEFT JOIN users u ON u.id = f.uploaded_by
"""


def file_row(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    return conn.execute(f"SELECT {_FILE_COLUMNS} {_FILE_FROM} WHERE f.id = ?", (file_id,)).fetchone()


def browse(conn: sqlite3.Connection, access: Access, collection_id: int, folder_id: int | None) -> dict:
    access.read(collection_id)
    require_folder(conn, collection_id, folder_id)
    folders = conn.execute(
        "SELECT id, name, created_at, (SELECT count(*) FROM files x WHERE x.folder_id = folders.id "
        "AND x.deleted_at IS NULL) + (SELECT count(*) FROM folders y WHERE y.parent_id = folders.id "
        "AND y.deleted_at IS NULL) AS items FROM folders WHERE collection_id = ? AND COALESCE(parent_id, 0) = ? "
        "AND deleted_at IS NULL ORDER BY name COLLATE NOCASE", (collection_id, folder_id or 0)).fetchall()
    files = conn.execute(
        f"SELECT {_FILE_COLUMNS} {_FILE_FROM} WHERE f.collection_id = ? AND COALESCE(f.folder_id, 0) = ? "
        "AND f.deleted_at IS NULL ORDER BY f.display_name COLLATE NOCASE", (collection_id, folder_id or 0)).fetchall()
    return {"folders": folders, "files": files, "path": folder_path(conn, folder_id),
            "can_write": collection_id in access.writable}


def folder_tree(conn: sqlite3.Connection, access: Access, collection_id: int) -> list[dict]:
    access.read(collection_id)
    live = live_folder_ids(conn, collection_id)
    return [{"id": r["id"], "parent_id": r["parent_id"], "name": r["name"]}
            for r in conn.execute("SELECT * FROM folders WHERE collection_id = ? ORDER BY name COLLATE NOCASE",
                                  (collection_id,)) if r["id"] in live]


def search_names(conn: sqlite3.Connection, access: Access, q: str, limit: int = 200) -> dict:
    """Files and folders whose name contains `q`, anywhere the user can read."""
    q = (q or "").strip()
    if not q or not access.readable:
        return {"folders": [], "files": []}
    marks = ",".join("?" * len(access.readable))
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    live = {cid: live_folder_ids(conn, cid) for cid in access.readable}
    folders = [r for r in conn.execute(
        f"SELECT id, collection_id, parent_id, name FROM folders WHERE collection_id IN ({marks}) "
        "AND name LIKE ? ESCAPE '\\' ORDER BY name COLLATE NOCASE", (*access.readable, like))
        if r["id"] in live[r["collection_id"]]][:limit]
    files = [r for r in conn.execute(
        f"SELECT {_FILE_COLUMNS} {_FILE_FROM} WHERE f.collection_id IN ({marks}) AND f.deleted_at IS NULL "
        "AND f.display_name LIKE ? ESCAPE '\\' ORDER BY f.display_name COLLATE NOCASE", (*access.readable, like))
        if r["folder_id"] is None or r["folder_id"] in live[r["collection_id"]]][:limit]
    return {"folders": folders, "files": files}


# ------------------------------------------------------------- search scope
def _sync_scope(conn: sqlite3.Connection, file_ids: list[int]) -> None:
    """Point each file's chunks and vectors at where the file now is."""
    live_cache: dict[int, set[int]] = {}
    for file_id in file_ids:
        row = conn.execute("SELECT collection_id, folder_id, deleted_at FROM files WHERE id = ?",
                           (file_id,)).fetchone()
        if row is None:
            continue
        cid, fid = row["collection_id"], row["folder_id"]
        if cid not in live_cache:
            live_cache[cid] = live_folder_ids(conn, cid)
        hidden = row["deleted_at"] is not None or (fid is not None and fid not in live_cache[cid])
        scope = (TRASH_COLLECTION if hidden else cid, fid or 0)
        conn.execute("UPDATE chunks SET collection_id = ?, folder_id = ? WHERE file_id = ?", (*scope, file_id))
        conn.execute("UPDATE chunk_vec SET collection_id = ?, folder_id = ? "
                     "WHERE rowid IN (SELECT id FROM chunks WHERE file_id = ?)", (*scope, file_id))


def search_scope(conn: sqlite3.Connection, file_id: int) -> tuple[int, int]:
    """(collection_id, folder_id) that a file's new chunks should carry."""
    row = conn.execute("SELECT collection_id, folder_id, deleted_at FROM files WHERE id = ?", (file_id,)).fetchone()
    hidden = row["deleted_at"] is not None or not is_live_folder(conn, row["folder_id"])
    return (TRASH_COLLECTION if hidden else row["collection_id"]), (row["folder_id"] or 0)


def _subtree_file_ids(conn: sqlite3.Connection, folder_ids: list[int]) -> list[int]:
    if not folder_ids:
        return []
    marks = ",".join("?" * len(folder_ids))
    return [r[0] for r in conn.execute(f"SELECT id FROM files WHERE folder_id IN ({marks})", folder_ids)]


# --------------------------------------------------------------- operations
def _live_file(conn: sqlite3.Connection, access: Access, file_id: int, *, write: bool) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    if row is None:
        raise NotFound("File not found")
    (access.write if write else access.read)(row["collection_id"])
    if row["deleted_at"] is not None or not is_live_folder(conn, row["folder_id"]):
        raise NotFound("File not found")
    return row


def _live_folder(conn: sqlite3.Connection, access: Access, folder_id: int, *, write: bool) -> sqlite3.Row:
    row = _folder(conn, folder_id)
    (access.write if write else access.read)(row["collection_id"])
    if not is_live_folder(conn, folder_id):
        raise NotFound("Folder not found")
    return row


def rename(conn: sqlite3.Connection, access: Access, item: Item, name: str) -> str:
    name = clean_name(name)
    with transaction(conn):
        if item.type == "folder":
            row = _live_folder(conn, access, item.id, write=True)
            if _taken(conn, row["collection_id"], row["parent_id"], name, "folder", item.id):
                raise Conflict(f"There is already a folder called “{name}” here.")
            conn.execute("UPDATE folders SET name = ? WHERE id = ?", (name, item.id))
        else:
            row = _live_file(conn, access, item.id, write=True)
            if _taken(conn, row["collection_id"], row["folder_id"], name, "file", item.id):
                raise Conflict(f"There is already a file called “{name}” here.")
            conn.execute("UPDATE files SET display_name = ?, updated_at = datetime('now') WHERE id = ?",
                         (name, item.id))
    return name


def _each(items: list[Item], op) -> list[Result]:
    results = []
    for item in items:
        try:
            extra = op(item) or {}
            results.append(Result(item, extra=extra))
        except FsError as exc:
            results.append(Result(item, ok=False, error=str(exc), status=exc.status))
    return results


def move(conn: sqlite3.Connection, access: Access, items: list[Item], collection_id: int,
         folder_id: int | None) -> list[Result]:
    access.write(collection_id)

    def one(item: Item) -> dict:
        with transaction(conn):
            require_folder(conn, collection_id, folder_id)
            if item.type == "file":
                row = _live_file(conn, access, item.id, write=True)
                if row["collection_id"] == collection_id and row["folder_id"] == folder_id:
                    return {"name": row["display_name"]}
                name = unique_name(conn, collection_id, folder_id, row["display_name"], "file")
                conn.execute("UPDATE files SET collection_id = ?, folder_id = ?, display_name = ?, "
                             "updated_at = datetime('now') WHERE id = ?", (collection_id, folder_id, name, item.id))
                _sync_scope(conn, [item.id])
                return {"name": name}
            row = _live_folder(conn, access, item.id, write=True)
            below = subtree(conn, item.id)
            if folder_id in below:
                raise FsError("A folder can't be moved into itself.")
            if row["collection_id"] == collection_id and row["parent_id"] == folder_id:
                return {"name": row["name"]}
            name = unique_name(conn, collection_id, folder_id, row["name"], "folder")
            conn.execute("UPDATE folders SET parent_id = ?, name = ? WHERE id = ?", (folder_id, name, item.id))
            if row["collection_id"] != collection_id:
                marks = ",".join("?" * len(below))
                conn.execute(f"UPDATE folders SET collection_id = ? WHERE id IN ({marks})", (collection_id, *below))
                conn.execute(f"UPDATE files SET collection_id = ? WHERE folder_id IN ({marks})", (collection_id, *below))
            _sync_scope(conn, _subtree_file_ids(conn, below))
            return {"name": name}

    return _each(items, one)


def _copy_file(conn: sqlite3.Connection, src: sqlite3.Row, collection_id: int, folder_id: int | None,
               user_id: int) -> int:
    name = unique_name(conn, collection_id, folder_id, src["display_name"], "file")
    stored_only = src["status"] == "stored"
    new_id = conn.execute(
        "INSERT INTO files(collection_id, folder_id, blob_sha256, display_name, uploaded_by, status, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (collection_id, folder_id, src["blob_sha256"], name, user_id,
         "stored" if stored_only else "queued", src["notes"])).lastrowid
    if not stored_only:
        # Ingestion finds the identical blob already indexed and copies its
        # chunks and vectors instead of embedding again.
        jobs.enqueue(conn, "ingest", new_id)
    return new_id


def copy(conn: sqlite3.Connection, access: Access, items: list[Item], collection_id: int,
         folder_id: int | None) -> list[Result]:
    access.write(collection_id)

    def one(item: Item) -> dict:
        with transaction(conn):
            require_folder(conn, collection_id, folder_id)
            if item.type == "file":
                src = _live_file(conn, access, item.id, write=False)
                return {"new_id": _copy_file(conn, src, collection_id, folder_id, access.user_id)}
            src = _live_folder(conn, access, item.id, write=False)
            if folder_id in subtree(conn, item.id):
                raise FsError("A folder can't be copied into itself.")
            live = live_folder_ids(conn, src["collection_id"])

            def copy_tree(source_id: int, parent_id: int | None, name: str) -> int:
                new_id = conn.execute(
                    "INSERT INTO folders(collection_id, parent_id, name, created_by) VALUES (?, ?, ?, ?)",
                    (collection_id, parent_id, name, access.user_id)).lastrowid
                for f in conn.execute("SELECT * FROM files WHERE folder_id = ? AND deleted_at IS NULL",
                                      (source_id,)).fetchall():
                    _copy_file(conn, f, collection_id, new_id, access.user_id)
                for child in conn.execute("SELECT id, name FROM folders WHERE parent_id = ?", (source_id,)).fetchall():
                    if child["id"] in live:
                        copy_tree(child["id"], new_id, child["name"])
                return new_id

            name = unique_name(conn, collection_id, folder_id, src["name"], "folder")
            return {"new_id": copy_tree(item.id, folder_id, name), "name": name}

    return _each(items, one)


def trash(conn: sqlite3.Connection, access: Access, items: list[Item]) -> list[Result]:
    def one(item: Item) -> None:
        with transaction(conn):
            if item.type == "file":
                _live_file(conn, access, item.id, write=True)
                conn.execute("UPDATE files SET deleted_at = datetime('now'), deleted_by = ? WHERE id = ?",
                             (access.user_id, item.id))
                _sync_scope(conn, [item.id])
            else:
                _live_folder(conn, access, item.id, write=True)
                conn.execute("UPDATE folders SET deleted_at = datetime('now'), deleted_by = ? WHERE id = ?",
                             (access.user_id, item.id))
                _sync_scope(conn, _subtree_file_ids(conn, subtree(conn, item.id)))

    return _each(items, one)


def _binned(conn: sqlite3.Connection, access: Access, item: Item) -> sqlite3.Row:
    table = "files" if item.type == "file" else "folders"
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (item.id,)).fetchone()
    if row is None:
        raise NotFound("Not found")
    access.write(row["collection_id"])
    if row["deleted_at"] is None:
        raise NotFound("That isn't in the recycle bin")
    return row


def restore(conn: sqlite3.Connection, access: Access, items: list[Item]) -> list[Result]:
    """Put items back where they were, or in the collection root if that
    folder is gone; renamed if the name has been taken meanwhile."""
    def one(item: Item) -> dict:
        with transaction(conn):
            row = _binned(conn, access, item)
            if item.type == "file":
                folder = row["folder_id"] if is_live_folder(conn, row["folder_id"]) else None
                name = unique_name(conn, row["collection_id"], folder, row["display_name"], "file", item.id)
                conn.execute("UPDATE files SET deleted_at = NULL, deleted_by = NULL, folder_id = ?, "
                             "display_name = ? WHERE id = ?", (folder, name, item.id))
                _sync_scope(conn, [item.id])
            else:
                parent = row["parent_id"] if is_live_folder(conn, row["parent_id"]) else None
                name = unique_name(conn, row["collection_id"], parent, row["name"], "folder", item.id)
                conn.execute("UPDATE folders SET deleted_at = NULL, deleted_by = NULL, parent_id = ?, name = ? "
                             "WHERE id = ?", (parent, name, item.id))
                _sync_scope(conn, _subtree_file_ids(conn, subtree(conn, item.id)))
            return {"name": name}

    return _each(items, one)


def _purge_folder(conn: sqlite3.Connection, settings: Settings, folder_id: int) -> None:
    """Delete a folder and its contents for good. Anything inside that was
    put in the bin on its own stays there as its own bin item."""
    below = subtree(conn, folder_id)
    for row in conn.execute(f"SELECT id FROM folders WHERE id IN ({','.join('?' * len(below))}) "
                            "AND id != ? AND deleted_at IS NOT NULL", (*below, folder_id)).fetchall():
        conn.execute("UPDATE folders SET parent_id = NULL WHERE id = ?", (row["id"],))
    below = subtree(conn, folder_id)
    marks = ",".join("?" * len(below))
    for row in conn.execute(f"SELECT id FROM files WHERE folder_id IN ({marks}) AND deleted_at IS NULL",
                            below).fetchall():
        store.delete_file(conn, settings, row["id"])
    # Subfolders cascade; separately binned files fall back to the root (SET NULL).
    conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))


def purge(conn: sqlite3.Connection, settings: Settings, access: Access, items: list[Item]) -> list[Result]:
    """Delete for good (only from the bin)."""
    def one(item: Item) -> None:
        _binned(conn, access, item)
        if item.type == "file":
            store.delete_file(conn, settings, item.id)
        else:
            _purge_folder(conn, settings, item.id)

    return _each(items, one)


def purge_expired(conn: sqlite3.Connection, settings: Settings, days: int) -> int:
    """Empty bin items older than `days`. Returns how many were removed."""
    cutoff = f"-{int(days)} days"
    old_files = [r[0] for r in conn.execute(
        "SELECT id FROM files WHERE deleted_at IS NOT NULL AND deleted_at < datetime('now', ?)", (cutoff,))]
    old_folders = [r[0] for r in conn.execute(
        "SELECT id FROM folders WHERE deleted_at IS NOT NULL AND deleted_at < datetime('now', ?)", (cutoff,))]
    for file_id in old_files:
        store.delete_file(conn, settings, file_id)
    for folder_id in old_folders:
        if conn.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone():
            _purge_folder(conn, settings, folder_id)
    return len(old_files) + len(old_folders)


def list_trash(conn: sqlite3.Connection, access: Access, collection_id: int) -> dict:
    access.write(collection_id)

    def where(folder_id: int | None) -> str:
        try:
            return "/" + "/".join(p["name"] for p in folder_path(conn, folder_id))
        except NotFound:
            return "/"

    folders = [{"id": r["id"], "name": r["name"], "deleted_at": r["deleted_at"], "deleted_by": r["username"],
                "original_path": where(r["parent_id"])}
               for r in conn.execute(
                   "SELECT f.*, u.username FROM folders f LEFT JOIN users u ON u.id = f.deleted_by "
                   "WHERE f.collection_id = ? AND f.deleted_at IS NOT NULL ORDER BY f.deleted_at DESC",
                   (collection_id,))]
    files = [{"id": r["id"], "name": r["display_name"], "deleted_at": r["deleted_at"], "deleted_by": r["username"],
              "original_path": where(r["folder_id"]), "size": r["size"], "kind": r["kind"], "mime": r["mime"]}
             for r in conn.execute(
                 "SELECT f.*, b.size, b.kind, b.mime, u.username FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 "
                 "LEFT JOIN users u ON u.id = f.deleted_by "
                 "WHERE f.collection_id = ? AND f.deleted_at IS NOT NULL ORDER BY f.deleted_at DESC",
                 (collection_id,))]
    return {"folders": folders, "files": files}


def trash_count(conn: sqlite3.Connection, collection_id: int) -> int:
    return conn.execute(
        "SELECT (SELECT count(*) FROM files WHERE collection_id = :c AND deleted_at IS NOT NULL) + "
        "(SELECT count(*) FROM folders WHERE collection_id = :c AND deleted_at IS NOT NULL)",
        {"c": collection_id}).fetchone()[0]


def scope_folders(conn: sqlite3.Connection, access: Access, collection_id: int, folder_id: int | None) -> set[int] | None:
    """Folder ids for a "search in this folder" question: the folder and its
    live subfolders. None means the whole collection."""
    access.read(collection_id)
    if folder_id is None:
        return None
    require_folder(conn, collection_id, folder_id)
    live = live_folder_ids(conn, collection_id)
    return {f for f in subtree(conn, folder_id) if f in live}
