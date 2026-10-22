"""The file manager: folders, rename, move, copy and the recycle bin.

Folders live inside a collection (a user's inbox or a library); NULL folder
means the collection root. Access is per location (permissions.reach): a
person may read or change some folders of a library and not others. Changing
an item needs write access where it is; a folder also needs write access to
everything inside it, so nobody can move or bin what they couldn't change.

Search scope travels with each file's chunks and vectors (collection_id,
folder_id), so every operation that changes where a file lives, or whether
it is in the bin, calls `_sync_scope` in the same transaction. A file in the
bin, or under a folder in the bin, gets collection 0, which no search allows.

Only the item that was put in the bin gets `deleted_at`; its contents keep
their place and are hidden because an ancestor is in the bin, so restoring a
folder brings everything back as it was.

Collections on the SMB share (share.py) are real folders: every change is
made to the database first and to the disk last, in one transaction, so a
disk error rolls the database back. Binned share items move to the trash
directory beside the share.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable

from mnemerynne import jobs, permissions, share
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.ingest import store

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


class Unavailable(FsError):
    status = 503


@dataclass
class Access:
    """What one user may read and write, looked up once per request:
    permissions.reach() per collection."""
    user_id: int
    reaches: dict[int, permissions.Reach]
    # Re-reads `reaches` when a folder turns up that didn't exist when they were
    # read (one made since, e.g. by a folder upload); None for a fixed snapshot.
    reload: Callable[[], dict[int, permissions.Reach]] | None = field(default=None, repr=False)
    # Claude's view: files people took out of it (files.claude_hidden) don't count as readable.
    claude: bool = False

    @classmethod
    def of(cls, conn: sqlite3.Connection, user_id: int) -> "Access":
        return cls(user_id, permissions.reach(conn, user_id), lambda: permissions.reach(conn, user_id))

    @classmethod
    def for_claude(cls, conn: sqlite3.Connection, user_id: int) -> "Access":
        """Read-only access to what the user may search from Claude."""
        return cls(user_id, permissions.claude_reach(conn, user_id), lambda: permissions.claude_reach(conn, user_id),
                   claude=True)

    def _reach(self, collection_id: int, folder_id: int | None = None) -> permissions.Reach | None:
        r = self.reaches.get(collection_id)
        if self.reload and folder_id is not None and (r is None or folder_id not in r.parents):
            self.reaches = self.reload()
            r = self.reaches.get(collection_id)
        return r

    @property
    def readable(self) -> set[int]:
        """Collections the user can read anything in."""
        return {cid for cid, r in self.reaches.items() if r.root_read or r.read}

    @property
    def writable(self) -> set[int]:
        """Collections the user can change anything in."""
        return {cid for cid, r in self.reaches.items() if r.any_write}

    def can_read(self, collection_id: int, folder_id: int | None) -> bool:
        r = self._reach(collection_id, folder_id)
        return r is not None and r.can_read(folder_id)

    def can_write(self, collection_id: int, folder_id: int | None) -> bool:
        r = self._reach(collection_id, folder_id)
        return r is not None and r.can_write(folder_id)

    def can_read_file(self, row) -> bool:
        """A file row (with collection_id, folder_id, claude_hidden): readable here, and for Claude not taken
        out of its view."""
        if self.claude and row["claude_hidden"]:
            return False
        return self.can_read(row["collection_id"], row["folder_id"])

    def visible(self, collection_id: int, folder_ids=()) -> set[int]:
        """Folders the user may see (to read them or reach one below). Pass the
        ids about to be filtered so that ones newer than `reaches` are known."""
        r = self.reaches.get(collection_id)
        unknown = next((f for f in folder_ids if r is None or f not in r.parents), None)
        if unknown is not None:
            r = self._reach(collection_id, unknown)
        return r.visible() if r else set()

    def see(self, collection_id: int, folder_id: int | None) -> None:
        """May open this place to look around (maybe only to reach a folder below)."""
        self._reach(collection_id, folder_id)
        # Same 404 whether it doesn't exist or isn't yours: no existence leaks.
        if collection_id not in self.readable or (folder_id is not None and folder_id not in self.visible(collection_id)):
            raise NotFound("Not found")

    def read(self, collection_id: int, folder_id: int | None) -> None:
        if not self.can_read(collection_id, folder_id):
            raise NotFound("Not found")

    def write(self, collection_id: int, folder_id: int | None) -> None:
        self.see(collection_id, folder_id)
        if not self.can_write(collection_id, folder_id):
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


def share_name(name: str) -> str:
    """Names in share collections must also be valid for Windows users."""
    try:
        return share.check_name(name)
    except ValueError as exc:
        raise FsError(str(exc)) from None


def _on_share(conn: sqlite3.Connection, collection_id: int) -> bool:
    return share.is_share(conn, collection_id)


def _disk(fn):
    """Run a disk operation, turning share errors into refusals (the caller's
    transaction then rolls back)."""
    try:
        return fn()
    except share.ShareUnavailable as exc:
        raise Unavailable(str(exc)) from None
    except share.ShareError as exc:
        raise FsError(str(exc)) from None


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
    access.write(collection_id, parent_id)
    name = clean_name(name)
    on_share = _on_share(conn, collection_id)
    if on_share:
        share_name(name)
    with transaction(conn):
        require_folder(conn, collection_id, parent_id)
        if _taken(conn, collection_id, parent_id, name, "folder"):
            raise Conflict(f"There is already a folder called “{name}” here.")
        folder_id = conn.execute(
            "INSERT INTO folders(collection_id, parent_id, name, created_by) VALUES (?, ?, ?, ?)",
            (collection_id, parent_id, name, access.user_id)).lastrowid
        if on_share:
            _disk(lambda: share.make_dir(share.folder_dir(conn, collection_id, folder_id)))
        return folder_id


def ensure_path(conn: sqlite3.Connection, collection_id: int, parent_id: int | None, parts: list[str],
                user_id: int | None) -> int | None:
    """The folder at parent/parts[0]/parts[1]…, creating what is missing.

    Runs inside the caller's transaction (or autocommit). Used by folder
    uploads and imports; the caller has checked write access.
    """
    folder_id = parent_id
    on_share = _on_share(conn, collection_id)
    for part in parts:
        name = clean_name(part)
        if on_share:
            share_name(name)
        row = conn.execute(
            "SELECT id FROM folders WHERE collection_id = ? AND COALESCE(parent_id, 0) = ? "
            "AND lower(name) = lower(?) AND deleted_at IS NULL", (collection_id, folder_id or 0, name)).fetchone()
        folder_id = row["id"] if row else conn.execute(
            "INSERT INTO folders(collection_id, parent_id, name, created_by) VALUES (?, ?, ?, ?)",
            (collection_id, folder_id, name, user_id)).lastrowid
        if on_share and row is None:
            _disk(lambda: share.make_dir(share.folder_dir(conn, collection_id, folder_id)))
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
    f.created_at, f.updated_at, f.deleted_at, f.uploaded_by, f.edited_via, f.claude_hidden, b.kind, b.mime, b.size, b.unit_count,
    b.rel_path, b.ocr_rel_path, u.username AS uploaded_by_name
"""
_FILE_FROM = """
    FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 LEFT JOIN users u ON u.id = f.uploaded_by
"""


def file_row(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    return conn.execute(f"SELECT {_FILE_COLUMNS} {_FILE_FROM} WHERE f.id = ?", (file_id,)).fetchone()


def browse(conn: sqlite3.Connection, access: Access, collection_id: int, folder_id: int | None) -> dict:
    """A place's subfolders and files. Someone who may only open a folder deeper
    down sees the folders on the way to it, but no files beside them."""
    access.see(collection_id, folder_id)
    require_folder(conn, collection_id, folder_id)
    rows = conn.execute(
        "SELECT id, name, created_at, role, (SELECT count(*) FROM files x WHERE x.folder_id = folders.id "
        "AND x.deleted_at IS NULL) + (SELECT count(*) FROM folders y WHERE y.parent_id = folders.id "
        "AND y.deleted_at IS NULL) AS items FROM folders WHERE collection_id = ? AND COALESCE(parent_id, 0) = ? "
        "AND deleted_at IS NULL ORDER BY name COLLATE NOCASE", (collection_id, folder_id or 0)).fetchall()
    visible = access.visible(collection_id, [r["id"] for r in rows])
    folders = [r for r in rows if r["id"] in visible]
    files = conn.execute(
        f"SELECT {_FILE_COLUMNS} {_FILE_FROM} WHERE f.collection_id = ? AND COALESCE(f.folder_id, 0) = ? "
        "AND f.deleted_at IS NULL ORDER BY f.display_name COLLATE NOCASE",
        (collection_id, folder_id or 0)).fetchall() if access.can_read(collection_id, folder_id) else []
    if access.claude:
        files = [f for f in files if not f["claude_hidden"]]
    return {"folders": folders, "files": files, "path": folder_path(conn, folder_id),
            "can_write": access.can_write(collection_id, folder_id)}


def folder_tree(conn: sqlite3.Connection, access: Access, collection_id: int) -> list[dict]:
    access.see(collection_id, None)
    live = live_folder_ids(conn, collection_id)
    shown = live & access.visible(collection_id, live)
    return [{"id": r["id"], "parent_id": r["parent_id"], "name": r["name"],
             "can_write": access.can_write(collection_id, r["id"]), "role": r["role"]}
            for r in conn.execute("SELECT * FROM folders WHERE collection_id = ? ORDER BY name COLLATE NOCASE",
                                  (collection_id,)) if r["id"] in shown]


def search_names(conn: sqlite3.Connection, access: Access, q: str, limit: int = 200) -> dict:
    """Files and folders whose name contains `q`, anywhere the user can read."""
    q = (q or "").strip()
    readable = access.readable
    if not q or not readable:
        return {"folders": [], "files": []}
    marks = ",".join("?" * len(readable))
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    live = {cid: live_folder_ids(conn, cid) for cid in readable}
    folders = [r for r in conn.execute(
        f"SELECT id, collection_id, parent_id, name FROM folders WHERE collection_id IN ({marks}) "
        "AND name LIKE ? ESCAPE '\\' ORDER BY name COLLATE NOCASE", (*readable, like))
        if r["id"] in live[r["collection_id"]] and access.can_read(r["collection_id"], r["id"])][:limit]
    files = [r for r in conn.execute(
        f"SELECT {_FILE_COLUMNS} {_FILE_FROM} WHERE f.collection_id IN ({marks}) AND f.deleted_at IS NULL "
        "AND f.display_name LIKE ? ESCAPE '\\' ORDER BY f.display_name COLLATE NOCASE", (*readable, like))
        if (r["folder_id"] is None or r["folder_id"] in live[r["collection_id"]]) and access.can_read_file(r)][:limit]
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


def claude_hidden(conn: sqlite3.Connection, file_id: int) -> int:
    """files.claude_hidden, which a file's new chunks and vectors carry."""
    row = conn.execute("SELECT claude_hidden FROM files WHERE id = ?", (file_id,)).fetchone()
    return int(row["claude_hidden"]) if row else 0


def set_file_claude(conn: sqlite3.Connection, access: Access, file_id: int, visible: bool) -> None:
    """Show a file to Claude again, or take it out of Claude's view (with its chunks and vectors).
    Only people who can change the place it's in."""
    row = file_row(conn, file_id)
    if row is None or row["deleted_at"] is not None or not access.can_read(row["collection_id"], row["folder_id"]):
        raise NotFound("No such file.")
    if not access.can_write(row["collection_id"], row["folder_id"]):
        raise Forbidden("Only people who can change this folder can change what Claude sees in it.")
    hidden = 0 if visible else 1
    with transaction(conn):
        conn.execute("UPDATE files SET claude_hidden = ? WHERE id = ?", (hidden, file_id))
        conn.execute("UPDATE chunks SET claude_hidden = ? WHERE file_id = ?", (hidden, file_id))
        conn.execute("UPDATE chunk_vec SET claude_hidden = ? WHERE rowid IN (SELECT id FROM chunks WHERE file_id = ?)",
                     (hidden, file_id))


def set_folder_claude(conn: sqlite3.Connection, access: Access, collection_id: int, folder_id: int,
                      visible: bool) -> None:
    """Show a folder (and what's in it) to Claude again, or take it out of Claude's view."""
    row = conn.execute("SELECT id FROM folders WHERE id = ? AND collection_id = ? AND deleted_at IS NULL",
                       (folder_id, collection_id)).fetchone()
    if row is None or not access.can_read(collection_id, folder_id):
        raise NotFound("No such folder.")
    if not access.can_write(collection_id, folder_id):
        raise Forbidden("Only people who can change this folder can change what Claude sees in it.")
    with transaction(conn):
        conn.execute("UPDATE folders SET claude_hidden = ? WHERE id = ?", (0 if visible else 1, folder_id))


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
    try:
        (access.write if write else access.read)(row["collection_id"], row["folder_id"])
    except NotFound:
        raise NotFound("File not found") from None
    if row["deleted_at"] is not None or not is_live_folder(conn, row["folder_id"]):
        raise NotFound("File not found")
    return row


def _write_whole_folder(conn: sqlite3.Connection, access: Access, row: sqlite3.Row) -> None:
    """Changing a folder (rename, move, bin) needs write access where it sits
    and to every folder inside it."""
    cid = row["collection_id"]
    access.write(cid, row["parent_id"])
    blocked = [f for f in subtree(conn, row["id"]) if not access.can_write(cid, f)]
    if blocked:
        raise Forbidden("You can't change this folder: you don't have edit access to everything in it.")


def _live_folder(conn: sqlite3.Connection, access: Access, folder_id: int, *, write: bool) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if row is None or not access.can_read(row["collection_id"], folder_id):
        raise NotFound("Folder not found")
    if write:
        _write_whole_folder(conn, access, row)
    if not is_live_folder(conn, folder_id):
        raise NotFound("Folder not found")
    return row


def rename(conn: sqlite3.Connection, access: Access, item: Item, name: str) -> str:
    name = clean_name(name)
    with transaction(conn):
        if item.type == "folder":
            row = _live_folder(conn, access, item.id, write=True)
            on_share = _on_share(conn, row["collection_id"])
            if on_share:
                share_name(name)
            if _taken(conn, row["collection_id"], row["parent_id"], name, "folder", item.id):
                raise Conflict(f"There is already a folder called “{name}” here.")
            before = share.folder_dir(conn, row["collection_id"], item.id) if on_share else None
            conn.execute("UPDATE folders SET name = ? WHERE id = ?", (name, item.id))
            if before is not None:
                _disk(lambda: share.rename(before, share.folder_dir(conn, row["collection_id"], item.id)))
        else:
            row = _live_file(conn, access, item.id, write=True)
            on_share = _on_share(conn, row["collection_id"])
            if on_share:
                share_name(name)
            if _taken(conn, row["collection_id"], row["folder_id"], name, "file", item.id):
                raise Conflict(f"There is already a file called “{name}” here.")
            before = share.file_path(conn, item.id) if on_share else None
            conn.execute("UPDATE files SET display_name = ?, updated_at = datetime('now') WHERE id = ?",
                         (name, item.id))
            if before is not None:
                after = share.file_path(conn, item.id)
                _disk(lambda: share.rename(before, after))
                share.record_stat(conn, item.id, after)
    return name


def _settings() -> Settings:
    rt = share.current()
    if rt is None:
        raise FsError("The share isn't configured on this server.")
    return rt.settings


def _bytes_to_blob(conn: sqlite3.Connection, file_id: int, src) -> None:
    """Make sure a file's bytes are in blob storage (it is leaving the share)."""
    row = conn.execute("SELECT b.rel_path FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.id = ?",
                       (file_id,)).fetchone()
    dest = store.blob_path(_settings(), row["rel_path"])
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            share.copy_bytes(src, dest)   # the share's label and ACLs stay behind
        except OSError as exc:
            raise FsError(f"Couldn't copy the file off the share: {exc.strerror or exc}") from None


def _clear_disk_stat(conn: sqlite3.Connection, file_id: int) -> None:
    conn.execute("UPDATE files SET disk_size = NULL, disk_mtime_ns = NULL, disk_inode = NULL WHERE id = ?",
                 (file_id,))


def _each(items: list[Item], op) -> list[Result]:
    results = []
    for item in items:
        try:
            extra = op(item) or {}
            results.append(Result(item, extra=extra))
        except FsError as exc:
            results.append(Result(item, ok=False, error=str(exc), status=exc.status))
        except share.ShareError as exc:   # e.g. the share isn't mounted
            results.append(Result(item, ok=False, error=str(exc), status=503))
    return results


def move(conn: sqlite3.Connection, access: Access, items: list[Item], collection_id: int,
         folder_id: int | None) -> list[Result]:
    access.write(collection_id, folder_id)
    leftovers: list = []   # share copies of files that moved off the share

    def one(item: Item) -> dict:
        with transaction(conn):
            require_folder(conn, collection_id, folder_id)
            if item.type == "file":
                row = _live_file(conn, access, item.id, write=True)
                if row["collection_id"] == collection_id and row["folder_id"] == folder_id:
                    return {"name": row["display_name"]}
                name = unique_name(conn, collection_id, folder_id, row["display_name"], "file")
                src_share, dst_share = _on_share(conn, row["collection_id"]), _on_share(conn, collection_id)
                if dst_share:
                    share_name(name)
                src = store.content_path(conn, _settings(), item.id) if (src_share or dst_share) else None
                conn.execute("UPDATE files SET collection_id = ?, folder_id = ?, display_name = ?, "
                             "updated_at = datetime('now') WHERE id = ?", (collection_id, folder_id, name, item.id))
                _sync_scope(conn, [item.id])
                if dst_share:
                    dest = share.folder_dir(conn, collection_id, folder_id) / name
                    _disk(lambda: share.rename(src, dest) if src_share else share.copy_file(src, dest))
                    share.record_stat(conn, item.id, dest)
                elif src_share:
                    _bytes_to_blob(conn, item.id, src)
                    _clear_disk_stat(conn, item.id)
                    leftovers.append(src)   # removed once the move is committed
                return {"name": name}
            row = _live_folder(conn, access, item.id, write=True)
            below = subtree(conn, item.id)
            if folder_id in below:
                raise FsError("A folder can't be moved into itself.")
            if row["collection_id"] == collection_id and row["parent_id"] == folder_id:
                return {"name": row["name"]}
            src_share, dst_share = _on_share(conn, row["collection_id"]), _on_share(conn, collection_id)
            if src_share != dst_share:
                raise FsError("Folders can't move between the share and a library that isn't on it yet; "
                              "copy it instead, then delete the original.")
            name = unique_name(conn, collection_id, folder_id, row["name"], "folder")
            if dst_share:
                share_name(name)
            before = share.folder_dir(conn, row["collection_id"], item.id) if src_share else None
            conn.execute("UPDATE folders SET parent_id = ?, name = ? WHERE id = ?", (folder_id, name, item.id))
            if row["collection_id"] != collection_id:
                marks = ",".join("?" * len(below))
                conn.execute(f"UPDATE folders SET collection_id = ? WHERE id IN ({marks})", (collection_id, *below))
                conn.execute(f"UPDATE files SET collection_id = ? WHERE folder_id IN ({marks})", (collection_id, *below))
            _sync_scope(conn, _subtree_file_ids(conn, below))
            if before is not None:
                _disk(lambda: share.rename(before, share.folder_dir(conn, collection_id, item.id)))
            return {"name": name}

    results = _each(items, one)
    for path in leftovers:
        try:
            share.remove(path)
        except share.ShareError:
            pass  # the file is safe off the share; the next scan tidies up the stray copy
    return results


def _copy_file(conn: sqlite3.Connection, src: sqlite3.Row, collection_id: int, folder_id: int | None,
               user_id: int) -> int:
    name = unique_name(conn, collection_id, folder_id, src["display_name"], "file")
    src_share, dst_share = _on_share(conn, src["collection_id"]), _on_share(conn, collection_id)
    if dst_share:
        share_name(name)
    stored_only = src["status"] == "stored"
    new_id = conn.execute(
        "INSERT INTO files(collection_id, folder_id, blob_sha256, display_name, uploaded_by, status, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (collection_id, folder_id, src["blob_sha256"], name, user_id,
         "stored" if stored_only else "queued", src["notes"])).lastrowid
    if src_share or dst_share:
        source = store.content_path(conn, _settings(), src["id"])
        if dst_share:
            dest = share.folder_dir(conn, collection_id, folder_id) / name
            _disk(lambda: share.copy_file(source, dest))
            share.record_stat(conn, new_id, dest)
        else:
            _bytes_to_blob(conn, new_id, source)
    if not stored_only:
        # Ingestion finds the identical blob already indexed and copies its
        # chunks and vectors instead of embedding again.
        jobs.enqueue(conn, "ingest", new_id)
    return new_id


def copy(conn: sqlite3.Connection, access: Access, items: list[Item], collection_id: int,
         folder_id: int | None) -> list[Result]:
    access.write(collection_id, folder_id)

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

            on_share = _on_share(conn, collection_id)

            def copy_tree(source_id: int, parent_id: int | None, name: str) -> int:
                if on_share:
                    share_name(name)
                new_id = conn.execute(
                    "INSERT INTO folders(collection_id, parent_id, name, created_by) VALUES (?, ?, ?, ?)",
                    (collection_id, parent_id, name, access.user_id)).lastrowid
                if on_share:
                    _disk(lambda: share.make_dir(share.folder_dir(conn, collection_id, new_id)))
                for f in conn.execute("SELECT * FROM files WHERE folder_id = ? AND deleted_at IS NULL",
                                      (source_id,)).fetchall():
                    _copy_file(conn, f, collection_id, new_id, access.user_id)
                for child in conn.execute("SELECT id, name FROM folders WHERE parent_id = ?", (source_id,)).fetchall():
                    # Only what the person can read comes along.
                    if child["id"] in live and access.can_read(src["collection_id"], child["id"]):
                        copy_tree(child["id"], new_id, child["name"])
                return new_id

            name = unique_name(conn, collection_id, folder_id, src["name"], "folder")
            return {"new_id": copy_tree(item.id, folder_id, name), "name": name}

    return _each(items, one)


def trash(conn: sqlite3.Connection, access: Access, items: list[Item]) -> list[Result]:
    def one(item: Item) -> None:
        with transaction(conn):
            if item.type == "file":
                row = _live_file(conn, access, item.id, write=True)
                on_share = _on_share(conn, row["collection_id"])
                before = share.file_path(conn, item.id) if on_share else None
                spot = share.trash_name("f", item.id, row["display_name"]) if on_share else None
                conn.execute("UPDATE files SET deleted_at = datetime('now'), deleted_by = ?, trash_path = ? "
                             "WHERE id = ?", (access.user_id, spot, item.id))
                _sync_scope(conn, [item.id])
                if before is not None:
                    _disk(lambda: share.rename(before, share.file_path(conn, item.id)))
            else:
                row = _live_folder(conn, access, item.id, write=True)
                on_share = _on_share(conn, row["collection_id"])
                before = share.folder_dir(conn, row["collection_id"], item.id) if on_share else None
                spot = share.trash_name("d", item.id, row["name"]) if on_share else None
                conn.execute("UPDATE folders SET deleted_at = datetime('now'), deleted_by = ?, trash_path = ? "
                             "WHERE id = ?", (access.user_id, spot, item.id))
                _sync_scope(conn, _subtree_file_ids(conn, subtree(conn, item.id)))
                if before is not None:
                    _disk(lambda: share.rename(before, share.folder_dir(conn, row["collection_id"], item.id)))

    return _each(items, one)


def _binned(conn: sqlite3.Connection, access: Access, item: Item) -> sqlite3.Row:
    table = "files" if item.type == "file" else "folders"
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (item.id,)).fetchone()
    if row is None:
        raise NotFound("Not found")
    cid = row["collection_id"]
    location = row["folder_id"] if item.type == "file" else row["parent_id"]
    if not access.can_write(cid, location) or (item.type == "folder" and not access.can_write(cid, item.id)):
        raise NotFound("Not found")
    if row["deleted_at"] is None:
        raise NotFound("That isn't in the recycle bin")
    return row


def restore(conn: sqlite3.Connection, access: Access, items: list[Item]) -> list[Result]:
    """Put items back where they were, or in the collection root if that
    folder is gone; renamed if the name has been taken meanwhile."""
    def one(item: Item) -> dict:
        with transaction(conn):
            row = _binned(conn, access, item)
            on_share = _on_share(conn, row["collection_id"])
            if item.type == "file":
                folder = row["folder_id"] if is_live_folder(conn, row["folder_id"]) else None
                name = unique_name(conn, row["collection_id"], folder, row["display_name"], "file", item.id)
                before = share.file_path(conn, item.id) if on_share else None
                conn.execute("UPDATE files SET deleted_at = NULL, deleted_by = NULL, trash_path = NULL, "
                             "folder_id = ?, display_name = ? WHERE id = ?", (folder, name, item.id))
                _sync_scope(conn, [item.id])
                if before is not None:
                    after = share.file_path(conn, item.id)
                    _disk(lambda: share.rename(before, after))
                    share.record_stat(conn, item.id, after)
            else:
                parent = row["parent_id"] if is_live_folder(conn, row["parent_id"]) else None
                name = unique_name(conn, row["collection_id"], parent, row["name"], "folder", item.id)
                before = share.folder_dir(conn, row["collection_id"], item.id) if on_share else None
                conn.execute("UPDATE folders SET deleted_at = NULL, deleted_by = NULL, trash_path = NULL, "
                             "parent_id = ?, name = ? WHERE id = ?", (parent, name, item.id))
                _sync_scope(conn, _subtree_file_ids(conn, subtree(conn, item.id)))
                if before is not None:
                    _disk(lambda: share.rename(before, share.folder_dir(conn, row["collection_id"], item.id)))
            return {"name": name}

    return _each(items, one)


def _purge_file(conn: sqlite3.Connection, settings: Settings, file_id: int) -> None:
    """Delete a binned file for good, with its bytes in the trash if on the share."""
    row = conn.execute("SELECT trash_path FROM files WHERE id = ?", (file_id,)).fetchone()
    with transaction(conn):
        store.delete_record(conn, settings, file_id)
        if row and row["trash_path"]:
            _disk(lambda: share.remove(settings.share_trash_dir / row["trash_path"]))


def _purge_folder(conn: sqlite3.Connection, settings: Settings, folder_id: int) -> None:
    """Delete a folder and its contents for good. Anything inside that was
    put in the bin on its own stays there as its own bin item."""
    with transaction(conn):   # all of it or none of it
        trash_path = conn.execute("SELECT trash_path FROM folders WHERE id = ?", (folder_id,)).fetchone()["trash_path"]
        below = subtree(conn, folder_id)
        for row in conn.execute(f"SELECT id FROM folders WHERE id IN ({','.join('?' * len(below))}) "
                                "AND id != ? AND deleted_at IS NOT NULL", (*below, folder_id)).fetchall():
            conn.execute("UPDATE folders SET parent_id = NULL WHERE id = ?", (row["id"],))
        below = subtree(conn, folder_id)
        marks = ",".join("?" * len(below))
        for row in conn.execute(f"SELECT id FROM files WHERE folder_id IN ({marks}) AND deleted_at IS NULL",
                                below).fetchall():
            store.delete_record(conn, settings, row["id"])
        # Subfolders cascade; separately binned files fall back to the root (SET NULL).
        conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        if trash_path:
            # Separately binned items inside already sit in their own trash places.
            _disk(lambda: share.remove(settings.share_trash_dir / trash_path))


def purge(conn: sqlite3.Connection, settings: Settings, access: Access, items: list[Item]) -> list[Result]:
    """Delete for good (only from the bin)."""
    def one(item: Item) -> None:
        _binned(conn, access, item)
        if item.type == "file":
            _purge_file(conn, settings, item.id)
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
        _purge_file(conn, settings, file_id)
    for folder_id in old_folders:
        if conn.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone():
            _purge_folder(conn, settings, folder_id)
    return len(old_files) + len(old_folders)


def list_trash(conn: sqlite3.Connection, access: Access, collection_id: int) -> dict:
    """The bin of a collection: what the user could put back (items from places they can change)."""
    if collection_id not in access.readable:
        raise NotFound("Not found")
    if collection_id not in access.writable:
        raise Forbidden("You can't change files here")

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
                   (collection_id,))
               if access.can_write(collection_id, r["parent_id"]) and access.can_write(collection_id, r["id"])]
    files = [{"id": r["id"], "name": r["display_name"], "deleted_at": r["deleted_at"], "deleted_by": r["username"],
              "original_path": where(r["folder_id"]), "size": r["size"], "kind": r["kind"], "mime": r["mime"]}
             for r in conn.execute(
                 "SELECT f.*, b.size, b.kind, b.mime, u.username FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 "
                 "LEFT JOIN users u ON u.id = f.deleted_by "
                 "WHERE f.collection_id = ? AND f.deleted_at IS NOT NULL ORDER BY f.deleted_at DESC",
                 (collection_id,))
             if access.can_write(collection_id, r["folder_id"])]
    return {"folders": folders, "files": files}


def trash_count(conn: sqlite3.Connection, collection_id: int) -> int:
    return conn.execute(
        "SELECT (SELECT count(*) FROM files WHERE collection_id = :c AND deleted_at IS NOT NULL) + "
        "(SELECT count(*) FROM folders WHERE collection_id = :c AND deleted_at IS NOT NULL)",
        {"c": collection_id}).fetchone()[0]


def scope_folders(conn: sqlite3.Connection, access: Access, collection_id: int, folder_id: int | None) -> set[int] | None:
    """Folder ids for a "search in this folder" question: the folder and its
    live subfolders. None means the whole collection. The search scope still
    applies on top, so subfolders the user can't read stay out."""
    access.see(collection_id, folder_id)
    if folder_id is None:
        return None
    require_folder(conn, collection_id, folder_id)
    live = live_folder_ids(conn, collection_id)
    return {f for f in subtree(conn, folder_id) if f in live}
