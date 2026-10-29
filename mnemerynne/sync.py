"""Desktop backup: the app on someone's computer copies folders into their File box on the share.

Each backed-up folder is a root, Users\\<name>\\Backups\\<computer>\\<label>. The app sends relative paths
("Photos/2026/cat.jpg") under a root; everything here stays inside that root. One way only: the app adds,
overwrites, moves and deletes, and the server keeps the old copy of an overwrite as a version and puts deletes in
the recycle bin. Backups are never read for search unless the person makes a root Searchable (folders.no_index).

Who may back up is meta 'backups_settings' (on/off and a group), checked when a token is made and on every call.
Plan: docs/plans/desktop-sync-client.md.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mnemerynne import editing, fs, group_refs, jobs, permissions, share
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.ingest import pipeline, store

SETTINGS_KEY = "backups_settings"
DEFAULTS = {"enabled": False, "group": ""}
BACKUPS = "Backups"
MAX_PUT_BYTES = 32 * 1024 * 1024    # one file in one request; bigger ones come in parts
MAX_PATH = 1024
MAX_DEPTH = 64
MANIFEST_PAGE = 1000


class SyncError(ValueError):
    """Refused; the message is safe to show, `status` is the HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Precondition(SyncError):
    """The file on the server isn't the version the app thinks it is (If-Match / If-None-Match)."""

    def __init__(self, message: str, current: str | None):
        super().__init__(message, 412)
        self.current = current


# ---------------------------------------------------------------- settings
def load_settings(conn: sqlite3.Connection) -> dict:
    """Backups' settings (meta 'backups_settings'), defaults filled in."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (SETTINGS_KEY,)).fetchone()
    try:
        saved = json.loads(row["value"]) if row else {}
    except ValueError:
        saved = {}
    return {k: saved.get(k, v) for k, v in DEFAULTS.items()}


def save_settings(conn: sqlite3.Connection, changes: dict) -> dict:
    """Store what changed (enabled, group; already checked) over what's saved; the new settings."""
    cfg = load_settings(conn) | {k: v for k, v in changes.items() if k in DEFAULTS and v is not None}
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (SETTINGS_KEY, json.dumps(cfg)))
    return cfg


def may_backup(conn: sqlite3.Connection, user: sqlite3.Row | None, directory) -> bool:
    """Backups on, and the person active and in the Backups group."""
    if user is None or not user["active"]:
        return False
    cfg = load_settings(conn)
    return bool(cfg["enabled"] and cfg["group"]) and group_refs.in_any(conn, user, directory, cfg["group"])


# ------------------------------------------------------------------- names
def clean_part(name: str, what: str = "name") -> str:
    """One name in a path, as it will be on the share; anything that would come back different is refused."""
    if not isinstance(name, str) or not name or name in (".", ".."):
        raise SyncError(f"Empty or relative {what} in the path.")
    if store.clean_display_name(name) != name:
        raise SyncError(f"“{name}” can't be stored as it is (control characters, extra spaces, or not NFC).")
    if share.is_junk(name):
        raise SyncError(f"“{name}” is a hidden or temporary name; the share ignores those.")
    try:
        share.check_name(name)
    except ValueError as exc:
        raise SyncError(str(exc)) from None
    return name


def split_path(path: str) -> list[str]:
    """'Photos/2026/cat.jpg' -> ['Photos', '2026', 'cat.jpg']; only ever below the root."""
    if not path or len(path) > MAX_PATH or path.startswith("/"):
        raise SyncError("A path is relative to the backed-up folder, like Photos/2026/cat.jpg.")
    parts = path.split("/")
    if len(parts) > MAX_DEPTH:
        raise SyncError(f"Paths can be at most {MAX_DEPTH} folders deep.")
    return [clean_part(p) for p in parts]


# ------------------------------------------------------------------- roots
@dataclass(frozen=True)
class Root:
    id: int
    user_id: int
    computer: str
    label: str
    collection_id: int
    folder_id: int


def _inbox(conn: sqlite3.Connection, user_id: int) -> int:
    inbox = permissions.private_collection_id(conn, user_id)
    if inbox is None or not share.is_share(conn, inbox):
        raise SyncError("Your File box isn't on the share yet; ask an admin to set it up.", 409)
    return inbox


def _ensure(conn: sqlite3.Connection, inbox: int, parent: int | None, name: str, user_id: int) -> tuple[int, bool]:
    """The folder parent/name, made if missing; (id, made now)."""
    had = conn.execute("SELECT id FROM folders WHERE collection_id = ? AND COALESCE(parent_id, 0) = ? "
                       "AND lower(name) = lower(?) AND deleted_at IS NULL", (inbox, parent or 0, name)).fetchone()
    if had:
        return had["id"], False
    return fs.ensure_path(conn, inbox, parent, [name], user_id), True


def register(conn: sqlite3.Connection, user_id: int, computer: str, label: str) -> Root:
    """Backups\\<computer>\\<label> in the person's File box, made if missing; the same root when called again.

    Backups and a new root start out not indexed and hidden from Claude."""
    computer, label = clean_part(computer, "computer name"), clean_part(label, "folder label")
    if len(computer) > 80 or len(label) > 80:
        raise SyncError("Computer names and labels are at most 80 characters.")
    inbox = _inbox(conn, user_id)
    try:
        with transaction(conn):
            backups, made = _ensure(conn, inbox, None, BACKUPS, user_id)
            if made:
                conn.execute("UPDATE folders SET no_index = 1, claude_hidden = 1 WHERE id = ?", (backups,))
            machine, _ = _ensure(conn, inbox, backups, computer, user_id)
            folder, made = _ensure(conn, inbox, machine, label, user_id)
            if made:
                conn.execute("UPDATE folders SET no_index = 1 WHERE id = ?", (folder,))
            conn.execute(
                "INSERT INTO sync_roots(user_id, computer, label, folder_id, last_seen_at) "
                "VALUES (?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(user_id, lower(computer), lower(label)) DO UPDATE SET folder_id = excluded.folder_id, "
                "last_seen_at = excluded.last_seen_at", (user_id, computer, label, folder))
    except fs.FsError as exc:
        raise SyncError(str(exc), exc.status) from None
    row = conn.execute("SELECT id FROM sync_roots WHERE user_id = ? AND lower(computer) = lower(?) "
                       "AND lower(label) = lower(?)", (user_id, computer, label)).fetchone()
    return get_root(conn, user_id, row["id"])


def get_root(conn: sqlite3.Connection, user_id: int, root_id: int) -> Root:
    """One of the person's roots, whose folder is still there (not binned)."""
    row = conn.execute("SELECT r.*, f.collection_id FROM sync_roots r LEFT JOIN folders f ON f.id = r.folder_id "
                       "WHERE r.id = ? AND r.user_id = ?", (root_id, user_id)).fetchone()
    if row is None:
        raise SyncError("There's no such backed-up folder.", 404)
    if row["folder_id"] is None or not fs.is_live_folder(conn, row["folder_id"]):
        raise SyncError("This backup's folder was deleted or moved to the recycle bin; register it again.", 409)
    return Root(row["id"], user_id, row["computer"], row["label"], row["collection_id"], row["folder_id"])


def roots(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """The person's backed-up folders, with how much is in each."""
    out = []
    for r in conn.execute("SELECT * FROM sync_roots WHERE user_id = ? ORDER BY computer COLLATE NOCASE, "
                          "label COLLATE NOCASE", (user_id,)):
        live = r["folder_id"] is not None and fs.is_live_folder(conn, r["folder_id"])
        files = size = 0
        if live:
            files, size = conn.execute(
                f"WITH RECURSIVE {_TREE} SELECT count(*), COALESCE(sum(b.size), 0) FROM files f "
                "JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.folder_id IN (SELECT id FROM t) "
                "AND f.deleted_at IS NULL", (r["folder_id"],)).fetchone()
        out.append({"id": r["id"], "computer": r["computer"], "label": r["label"], "folder_id": r["folder_id"],
                    "present": live, "searchable": live and not fs.not_indexed(conn, r["folder_id"]),
                    "files": files, "bytes": size, "last_sync": r["last_seen_at"]})
    return out


def touch(conn: sqlite3.Connection, root: Root) -> None:
    conn.execute("UPDATE sync_roots SET last_seen_at = datetime('now') WHERE id = ?", (root.id,))


# ---------------------------------------------------------------- manifest
# The root's folder and every live folder below it (binned folders stop the walk).
_TREE = ("t(id) AS (SELECT ? UNION ALL SELECT f.id FROM folders f JOIN t ON f.parent_id = t.id "
         "WHERE f.deleted_at IS NULL)")


def manifest(conn: sqlite3.Connection, root: Root, after: int = 0, limit: int = MANIFEST_PAGE) -> dict:
    """What's in the root: folders (on the first page) and files after `after` (a file id), oldest first.
    `next` is the cursor for the following page, or None at the end."""
    limit = max(1, min(limit, MANIFEST_PAGE))
    tree = {r["id"]: r for r in conn.execute(
        f"WITH RECURSIVE {_TREE} SELECT id, parent_id, name FROM folders WHERE id IN (SELECT id FROM t)",
        (root.folder_id,))}
    paths: dict[int, str] = {root.folder_id: ""}

    def path_of(fid: int) -> str:
        if fid not in paths:
            row = tree[fid]
            above = path_of(row["parent_id"])
            paths[fid] = f"{above}/{row['name']}" if above else row["name"]
        return paths[fid]

    folders = sorted(path_of(fid) for fid in tree if fid != root.folder_id) if after == 0 else []
    rows = conn.execute(
        f"WITH RECURSIVE {_TREE} SELECT f.id, f.folder_id, f.display_name, f.blob_sha256, f.disk_mtime_ns, b.size "
        "FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.folder_id IN (SELECT id FROM t) "
        "AND f.deleted_at IS NULL AND f.id > ? ORDER BY f.id LIMIT ?", (root.folder_id, after, limit)).fetchall()
    files = [{"path": f"{path_of(r['folder_id'])}/{r['display_name']}".lstrip("/"), "size": r["size"],
              "sha256": r["blob_sha256"], "version": r["blob_sha256"], "mtime_ns": r["disk_mtime_ns"]}
             for r in rows]
    return {"folders": folders, "files": files, "next": rows[-1]["id"] if len(rows) == limit else None}


# ------------------------------------------------------------------ lookups
def _find(conn: sqlite3.Connection, root: Root, parts: list[str]) -> fs.Item | None:
    """The live file or folder at parts below the root (names match case-insensitively, as on Windows)."""
    folder = root.folder_id
    for name in parts[:-1]:
        row = conn.execute("SELECT id FROM folders WHERE parent_id = ? AND lower(name) = lower(?) "
                           "AND deleted_at IS NULL", (folder, name)).fetchone()
        if row is None:
            return None
        folder = row["id"]
    last = parts[-1]
    row = conn.execute("SELECT id FROM files WHERE folder_id = ? AND lower(display_name) = lower(?) "
                       "AND deleted_at IS NULL", (folder, last)).fetchone()
    if row:
        return fs.Item("file", row["id"])
    row = conn.execute("SELECT id FROM folders WHERE parent_id = ? AND lower(name) = lower(?) "
                       "AND deleted_at IS NULL", (folder, last)).fetchone()
    return fs.Item("folder", row["id"]) if row else None


def _folder_for(conn: sqlite3.Connection, root: Root, dirs: list[str], user_id: int) -> int:
    """The folder for these parts below the root, made where missing (inside the caller's transaction)."""
    folder = root.folder_id
    for name in dirs:
        if conn.execute("SELECT 1 FROM files WHERE folder_id = ? AND lower(display_name) = lower(?) "
                        "AND deleted_at IS NULL", (folder, name)).fetchone():
            raise SyncError(f"“{name}” is a file on the server, not a folder.", 409)
        folder, _ = _ensure(conn, root.collection_id, folder, name, user_id)
    return folder


# -------------------------------------------------------------------- write
def _status(conn: sqlite3.Connection, folder_id: int, kind: str) -> tuple[str, str | None]:
    """(status, note) for a file landing here: not read when the backup isn't Searchable."""
    if kind == "other":
        return "stored", None
    if fs.not_indexed(conn, folder_id):
        return "stored", pipeline.NOT_INDEXED_NOTE
    return "queued", None


def _strip(tag: str | None) -> str | None:
    return tag.strip().strip('"').lower() if tag else None


def write_file(conn: sqlite3.Connection, settings: Settings, root: Root, path: str, upload: Path, sha256: str, *,
               user_id: int, token_id: int | None = None, mtime: float | None = None,
               if_match: str | None = None, if_none_match: str | None = None) -> dict:
    """Put a received file (`upload`, a temp file already checked against `sha256`) at `path` under the root.

    A new file needs `If-None-Match: *`; replacing one needs `If-Match` with the version (sha256) the app last
    saw, else Precondition (412) with the server's version. The old content is kept as a version. The upload is
    consumed (moved or deleted) either way."""
    try:
        return _write_file(conn, settings, root, split_path(path), upload, sha256.lower(), user_id=user_id,
                           token_id=token_id, mtime=mtime, if_match=_strip(if_match),
                           if_none_match=_strip(if_none_match))
    except fs.FsError as exc:
        raise SyncError(str(exc), exc.status) from None
    except share.ShareError as exc:
        raise SyncError(str(exc), 503 if isinstance(exc, share.ShareUnavailable) else 409) from None
    finally:
        upload.unlink(missing_ok=True)


def _write_file(conn, settings, root, parts, upload, sha, *, user_id, token_id, mtime, if_match, if_none_match):
    *dirs, name = parts
    if if_match is None and if_none_match != "*":
        raise SyncError("Send If-Match with the version you last saw, or If-None-Match: * for a new file.", 428)
    if mtime is not None:
        os.utime(upload, (mtime, mtime))
    kind, mime, size, note = store.inspect(upload, name, settings)
    with transaction(conn):
        folder = _folder_for(conn, root, dirs, user_id)
        if conn.execute("SELECT 1 FROM folders WHERE parent_id = ? AND lower(name) = lower(?) AND deleted_at IS NULL",
                        (folder, name)).fetchone():
            raise SyncError(f"“{name}” is a folder on the server, not a file.", 409)
        row = conn.execute("SELECT * FROM files WHERE folder_id = ? AND lower(display_name) = lower(?) "
                           "AND deleted_at IS NULL", (folder, name)).fetchone()
        current = row["blob_sha256"] if row else None
        if row is None and if_match is not None:
            raise Precondition(f"“{name}” isn't on the server any more.", None)
        if row is not None and (if_none_match == "*" or if_match != current):
            raise Precondition(f"“{name}” changed on the server since the app last saw it.", current)
        touch(conn, root)
        if row is not None and current == sha:
            return {"version": sha, "changed": False}
        conn.execute("INSERT OR IGNORE INTO blobs(sha256, kind, mime, size, rel_path) VALUES (?, ?, ?, ?, ?)",
                     (sha, kind, mime, size, store.blob_rel_path(sha, kind)))
        kind = conn.execute("SELECT kind FROM blobs WHERE sha256 = ?", (sha,)).fetchone()["kind"]
        status, stored_note = _status(conn, folder, kind)
        note = stored_note or note
        if row is None:
            file_id = conn.execute(
                "INSERT INTO files(collection_id, folder_id, blob_sha256, display_name, uploaded_by, status, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", (root.collection_id, folder, sha, name, user_id, status, note)).lastrowid
            dest = share.folder_dir(conn, root.collection_id, folder) / name
            share.place(upload, dest)      # last: if it fails, the transaction rolls back
        else:
            file_id = row["id"]
            dest = share.file_path(conn, file_id)
            ext = PurePosixPath(row["display_name"]).suffix.lower()
            kept = editing.keep_version_file(settings, file_id, dest, ext)
            tmp = dest.parent / f".mnemerynne-{secrets.token_hex(6)}.tmp"
            try:
                conn.execute("INSERT INTO file_versions(file_id, sha256, size, rel_path, created_by, via, token_id, "
                             "summary) VALUES (?, ?, ?, ?, ?, 'sync', ?, ?)",
                             (file_id, current, dest.stat().st_size, kept, user_id, token_id,
                              f"Backed up from {root.computer}"))
                conn.execute("UPDATE files SET blob_sha256 = ?, status = ?, error = NULL, progress = NULL, notes = ?, "
                             "updated_at = datetime('now'), edited_via = ? WHERE id = ?",
                             (sha, status, note, f"sync:{root.computer}", file_id))
                share._drop_unused_blob(conn, settings, current)
                share.place(upload, tmp)
                try:
                    os.replace(tmp, dest)
                except OSError as exc:
                    raise share._fail("save the file", exc) from exc
            except BaseException:
                tmp.unlink(missing_ok=True)
                editing.version_path(settings, kept).unlink(missing_ok=True)
                raise
        share.record_stat(conn, file_id, dest)
        if status == "queued":
            jobs.enqueue(conn, "ingest", file_id)
    return {"version": sha, "changed": True}


# ------------------------------------------------------------ delete, move
def delete(conn: sqlite3.Connection, root: Root, paths: list[str], user_id: int) -> list[dict]:
    """Move each path (a file or a folder) to the recycle bin. A path that's already gone counts as done."""
    access = fs.Access.of(conn, user_id)
    out = []
    for path in paths:
        try:
            item = _find(conn, root, split_path(path))
        except SyncError as exc:
            out.append({"path": path, "ok": False, "status": exc.status, "error": str(exc)})
            continue
        if item is None:
            out.append({"path": path, "ok": True, "gone": True})
            continue
        result = fs.trash(conn, access, [item])[0]
        out.append({"path": path, "ok": result.ok, "status": result.status, "error": result.error})
    if paths:
        touch(conn, root)
    return out


def move(conn: sqlite3.Connection, root: Root, moves: list[tuple[str, str]], user_id: int) -> list[dict]:
    """Rename or move each (from, to) within the root, keeping the file's id (and so its versions). The
    destination must not exist yet; its folders are made where missing."""
    access = fs.Access.of(conn, user_id)
    out = []
    for src, dest in moves:
        entry = {"from": src, "to": dest}
        try:
            out.append(entry | _move_one(conn, access, root, src, dest, user_id))
        except SyncError as exc:
            out.append(entry | {"ok": False, "status": exc.status, "error": str(exc)})
        except fs.FsError as exc:
            out.append(entry | {"ok": False, "status": exc.status, "error": str(exc)})
    if moves:
        touch(conn, root)
    return out


def _move_one(conn, access, root, src: str, dest: str, user_id: int) -> dict:
    src_parts, dest_parts = split_path(src), split_path(dest)
    item = _find(conn, root, src_parts)
    if item is None:
        raise SyncError(f"“{src}” isn't on the server.", 404)
    if src_parts[:-1] == dest_parts[:-1] and src_parts[-1].lower() == dest_parts[-1].lower() \
            and src_parts[-1] != dest_parts[-1]:
        found = None    # only the case changes: the same item, renamed in place
    else:
        found = _find(conn, root, dest_parts)
    if found is not None:
        raise SyncError(f"“{dest}” is already on the server.", 409)
    with transaction(conn):
        folder = _folder_for(conn, root, dest_parts[:-1], user_id)
    if item.type == "folder" and folder in fs.subtree(conn, item.id):
        raise SyncError("A folder can't be moved into itself.")
    result = fs.move(conn, access, [item], root.collection_id, folder)[0]
    if not result.ok:
        return {"ok": False, "status": result.status, "error": result.error}
    if result.extra.get("name") != dest_parts[-1]:
        fs.rename(conn, access, item, dest_parts[-1])
    return {"ok": True}
