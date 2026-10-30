"""Desktop backup: the app on someone's computer copies folders into their File box on the share.

Each backed-up folder is a root, Users\\<name>\\Backups\\<computer>\\<label>. The app sends relative paths
("Photos/2026/cat.jpg") under a root; everything here stays inside that root. One way only: the app adds,
overwrites, moves and deletes, and the server keeps the old copy of an overwrite as a version and puts deletes in
the recycle bin. Backups are never read for search unless the person makes a root Searchable (folders.no_index).

Who may back up is meta 'backups_settings' (on/off and a group), checked when a token is made and on every call.
Plan: docs/plans/desktop-sync-client.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

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


# ------------------------------------------------------------ backup host
# On MNEMERYNNE_BACKUP_URL's host only these answer: the app's API and the OAuth token calls it renews with.
HOST_PATHS = ("/api/sync/", "/oauth/token", "/oauth/revoke")


def backup_host(settings: Settings) -> str:
    """The backup host's name, lower case, without a port; "" when MNEMERYNNE_BACKUP_URL isn't set."""
    return (urlsplit((settings.backup_url or "").strip()).hostname or "").lower()


def check_config(settings: Settings) -> None:
    """At start: a set MNEMERYNNE_BACKUP_URL must be an https origin on a host of its own. Raises ValueError."""
    raw = (settings.backup_url or "").strip()
    if not raw:
        return
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.path not in ("", "/") or parts.query:
        raise ValueError("MNEMERYNNE_BACKUP_URL is an origin only, like https://bkp.example.org")
    if not settings.public_url:
        # The app signs in and downloads updates at the main address; it learns it from the backup host.
        raise ValueError("MNEMERYNNE_BACKUP_URL needs MNEMERYNNE_PUBLIC_URL set too")
    if backup_host(settings) == (urlsplit(settings.public_url).hostname or "").lower():
        raise ValueError("MNEMERYNNE_BACKUP_URL must be a different host from MNEMERYNNE_PUBLIC_URL")


class BackupHost:
    """ASGI middleware: on the backup host, everything but HOST_PATHS is 404 before any page or session runs."""

    def __init__(self, app, host: str) -> None:
        self.app, self.host = app, host

    async def __call__(self, scope, receive, send) -> None:
        if self.host and scope["type"] in ("http", "websocket"):
            raw = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k.lower() == b"host"), "")
            path = scope.get("path", "")
            if raw.rsplit(":", 1)[0].strip("[]").lower() == self.host and not (
                    path.startswith(HOST_PATHS[0]) or path in HOST_PATHS[1:]):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                body = b'{"detail": "Not found"}'
                await send({"type": "http.response.start", "status": 404,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


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


def all_roots(conn: sqlite3.Connection) -> list[dict]:
    """Everyone's backed-up folders, for admins (Access → Settings → Desktop backup, sync-status)."""
    out = []
    for user_id, username in conn.execute("SELECT DISTINCT r.user_id, u.username FROM sync_roots r "
                                          "JOIN users u ON u.id = r.user_id ORDER BY u.username COLLATE NOCASE"):
        out += [{"username": username, **r, "last_seen_at": r["last_sync"]} for r in roots(conn, user_id)]
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
            old_size = dest.stat().st_size
            # The old copy is kept as a version, unless it's too big for the versions folder.
            kept = editing.keep_version_file(settings, file_id, dest, ext) \
                if old_size <= settings.sync_version_max_bytes else None
            tmp = dest.parent / f".mnemerynne-{secrets.token_hex(6)}.tmp"
            try:
                if kept:
                    conn.execute("INSERT INTO file_versions(file_id, sha256, size, rel_path, created_by, via, "
                                 "token_id, summary) VALUES (?, ?, ?, ?, ?, 'sync', ?, ?)",
                                 (file_id, current, old_size, kept, user_id, token_id,
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
                if kept:
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


# ------------------------------------------------------------- downloads
def file_at(conn: sqlite3.Connection, root: Root, path: str) -> sqlite3.Row:
    """The live file at path under the root (Restore)."""
    item = _find(conn, root, split_path(path))
    if item is None or item.type != "file":
        raise SyncError(f"“{path}” isn't in the backup.", 404)
    return conn.execute("SELECT * FROM files WHERE id = ?", (item.id,)).fetchone()


def versions(conn: sqlite3.Connection, root: Root, path: str) -> list[dict]:
    """Earlier copies of a backed-up file, newest first."""
    row = file_at(conn, root, path)
    return [{"id": r["id"], "version": r["sha256"], "size": r["size"], "created_at": r["created_at"],
             "via": r["via"]} for r in conn.execute(
        "SELECT * FROM file_versions WHERE file_id = ? ORDER BY id DESC", (row["id"],))]


# --------------------------------------------------- uploads in parts
PART_BYTES = 32 * 1024 * 1024
UPLOAD_DAYS = 1


def _upload_file(settings: Settings, upload_id: str) -> Path:
    return settings.tmp_dir / "sync-uploads" / f"{upload_id}.part"


def start_upload(conn: sqlite3.Connection, settings: Settings, root: Root, path: str, size: int, sha256: str) -> dict:
    """A big file arriving in PART_BYTES parts. The same file to the same place again resumes the upload."""
    split_path(path)
    sha256 = (sha256 or "").lower()
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise SyncError("Give the file's SHA-256 in hex.")
    if size <= 0:
        raise SyncError("The file is empty.")
    if size > settings.max_sync_file_bytes:
        raise SyncError(f"Files over {settings.max_sync_file_bytes // 1024 ** 3} GB aren't backed up.", 413)
    row = conn.execute("SELECT id FROM sync_uploads WHERE user_id = ? AND root_id = ? AND path = ? AND size = ? "
                       "AND sha256 = ?", (root.user_id, root.id, path, size, sha256)).fetchone()
    if row and _upload_file(settings, row["id"]).exists():
        return upload_status(conn, settings, root.user_id, row["id"])
    upload_id = secrets.token_urlsafe(18)
    target = _upload_file(settings, upload_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as fh:
        fh.truncate(size)                     # sparse: parts land at their place in any order
    conn.execute("DELETE FROM sync_uploads WHERE user_id = ? AND root_id = ? AND path = ?", (root.user_id, root.id, path))
    conn.execute("INSERT INTO sync_uploads(id, user_id, root_id, path, size, sha256) VALUES (?, ?, ?, ?, ?, ?)",
                 (upload_id, root.user_id, root.id, path, size, sha256))
    return upload_status(conn, settings, root.user_id, upload_id)


def _upload(conn: sqlite3.Connection, user_id: int, upload_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM sync_uploads WHERE id = ? AND user_id = ?", (upload_id, user_id)).fetchone()
    if row is None:
        raise SyncError("That upload isn't here any more; start it again.", 404)
    return row


def upload_status(conn: sqlite3.Connection, settings: Settings, user_id: int, upload_id: str) -> dict:
    row = _upload(conn, user_id, upload_id)
    return {"id": row["id"], "root_id": row["root_id"], "path": row["path"], "size": row["size"],
            "part_size": PART_BYTES, "parts": json.loads(row["parts_received"])}


def part_length(size: int, n: int) -> int:
    """How long part n of a file of `size` bytes is (0: there's no such part)."""
    start = n * PART_BYTES
    return max(0, min(PART_BYTES, size - start)) if n >= 0 else 0


def store_part(conn: sqlite3.Connection, settings: Settings, user_id: int, upload_id: str, n: int, data: Path,
               sha256: str) -> dict:
    """Put a received part (a temp file, already checked against `sha256`) in its place."""
    row = _upload(conn, user_id, upload_id)
    length = part_length(row["size"], n)
    if length == 0:
        raise SyncError(f"Part {n} isn't part of this file.")
    if data.stat().st_size != length:
        raise SyncError(f"Part {n} should be {length} bytes.")
    with open(_upload_file(settings, upload_id), "r+b") as out, open(data, "rb") as part:
        out.seek(n * PART_BYTES)
        while block := part.read(1024 * 1024):
            out.write(block)
        out.flush()
        os.fsync(out.fileno())
    with transaction(conn):
        parts = set(json.loads(conn.execute("SELECT parts_received FROM sync_uploads WHERE id = ?",
                                            (upload_id,)).fetchone()[0]))
        parts.add(n)
        conn.execute("UPDATE sync_uploads SET parts_received = ? WHERE id = ?", (json.dumps(sorted(parts)), upload_id))
    return upload_status(conn, settings, user_id, upload_id)


def finish_upload(conn: sqlite3.Connection, settings: Settings, root_for, user_id: int, upload_id: str, *,
                  token_id: int | None = None, mtime: float | None = None, if_match: str | None = None,
                  if_none_match: str | None = None) -> dict:
    """Every part is here: check the whole file's hash and put it in place, as write_file does."""
    row = _upload(conn, user_id, upload_id)
    have = set(json.loads(row["parts_received"]))
    count = -(-row["size"] // PART_BYTES)
    missing = sorted(set(range(count)) - have)
    if missing:
        raise SyncError(f"Parts {', '.join(map(str, missing[:10]))} haven't arrived yet.", 409)
    path = _upload_file(settings, upload_id)
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(1024 * 1024):
            digest.update(block)
    if digest.hexdigest() != row["sha256"]:
        drop_upload(conn, settings, user_id, upload_id)
        raise SyncError("The file put together from its parts doesn't match its SHA-256; send it again.")
    root = root_for(row["root_id"])
    result = write_file(conn, settings, root, row["path"], path, row["sha256"], user_id=user_id, token_id=token_id,
                        mtime=mtime, if_match=if_match, if_none_match=if_none_match)
    conn.execute("DELETE FROM sync_uploads WHERE id = ?", (upload_id,))
    return result


def drop_upload(conn: sqlite3.Connection, settings: Settings, user_id: int, upload_id: str) -> None:
    _upload(conn, user_id, upload_id)
    conn.execute("DELETE FROM sync_uploads WHERE id = ?", (upload_id,))
    _upload_file(settings, upload_id).unlink(missing_ok=True)


def sweep_uploads(conn: sqlite3.Connection, settings: Settings) -> int:
    """Uploads nobody finished within a day, and part files without an upload (the worker, hourly)."""
    old = [r[0] for r in conn.execute("SELECT id FROM sync_uploads WHERE created_at < datetime('now', ?)",
                                      (f"-{UPLOAD_DAYS} days",))]
    for upload_id in old:
        conn.execute("DELETE FROM sync_uploads WHERE id = ?", (upload_id,))
        _upload_file(settings, upload_id).unlink(missing_ok=True)
    folder = settings.tmp_dir / "sync-uploads"
    if folder.is_dir():
        live = {r[0] for r in conn.execute("SELECT id FROM sync_uploads")}
        for f in folder.glob("*.part"):
            if f.stem not in live:
                f.unlink(missing_ok=True)
    return len(old)


# -------------------------------------------------------------- searchable
def set_searchable(conn: sqlite3.Connection, root: Root, on: bool) -> int:
    """Let Erynne search this backup, or stop. On: its documents are queued to be read (the worker takes them one
    at a time). Off: what was read is removed from search. Returns how many files changed."""
    file_ids = [r[0] for r in conn.execute(
        f"WITH RECURSIVE {_TREE} SELECT f.id FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 "
        "WHERE f.folder_id IN (SELECT id FROM t) AND f.deleted_at IS NULL AND b.kind != 'other'", (root.folder_id,))]
    with transaction(conn):
        conn.execute("UPDATE folders SET no_index = ? WHERE id = ?", (0 if on else 1, root.folder_id))
        changed = 0
        for file_id in file_ids:
            folder = conn.execute("SELECT folder_id FROM files WHERE id = ?", (file_id,)).fetchone()[0]
            if fs.not_indexed(conn, folder) != (not on):
                continue                      # a folder further down keeps its own setting
            if on:
                conn.execute("UPDATE files SET status = 'queued', notes = NULL, error = NULL, progress = NULL "
                             "WHERE id = ? AND status = 'stored'", (file_id,))
                jobs.enqueue(conn, "ingest", file_id)
            else:
                conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))   # vectors follow (trigger)
                conn.execute("UPDATE files SET status = 'stored', notes = ?, progress = NULL WHERE id = ?",
                             (pipeline.NOT_INDEXED_NOTE, file_id))
            changed += 1
    return changed


def rename_root(conn: sqlite3.Connection, root: Root, label: str) -> Root:
    """A new label: the folder under Backups\\<computer> is renamed (its files keep their ids and versions)."""
    label = clean_part(label, "folder label")
    if len(label) > 80:
        raise SyncError("Labels are at most 80 characters.")
    if label == root.label:
        return root
    if conn.execute("SELECT 1 FROM sync_roots WHERE user_id = ? AND lower(computer) = lower(?) AND lower(label) = "
                    "lower(?) AND id != ?", (root.user_id, root.computer, label, root.id)).fetchone():
        raise SyncError(f"Another backed-up folder from {root.computer} is already called “{label}”.", 409)
    try:
        fs.rename(conn, fs.Access.of(conn, root.user_id), fs.Item("folder", root.folder_id), label)
    except fs.FsError as exc:
        raise SyncError(str(exc), exc.status) from None
    conn.execute("UPDATE sync_roots SET label = ? WHERE id = ?", (label, root.id))
    return get_root(conn, root.user_id, root.id)


# ------------------------------------------------------------- the app itself
CLIENT_DIR = "backup-client"       # under data_dir: the installer and AppImage Preferences → Backups offers
_CLIENT_FILES = {"windows": re.compile(r"^Mnemerynne-Backup-Setup-(\d+(?:\.\d+){1,3})\.exe$"),
                 "linux": re.compile(r"^Mnemerynne_Backup-(\d+(?:\.\d+){1,3})-x86_64\.AppImage$")}


def client_downloads(settings: Settings) -> dict:
    """{"latest": "0.2.0", "files": {"windows": name, "linux": name}}: the newest build of each kind in
    <data_dir>/backup-client, and the newest version among them."""
    folder = settings.data_dir / CLIENT_DIR
    best: dict[str, tuple[tuple[int, ...], str]] = {}
    for f in (folder.iterdir() if folder.is_dir() else []):
        for kind, pattern in _CLIENT_FILES.items():
            m = pattern.match(f.name)
            if m:
                version = tuple(int(x) for x in m.group(1).split("."))
                if kind not in best or version > best[kind][0]:
                    best[kind] = (version, f.name)
    latest = max((v for v, _ in best.values()), default=None)
    return {"latest": ".".join(map(str, latest)) if latest else None, "files": {k: n for k, (_, n) in best.items()}}


def client_file(settings: Settings, name: str) -> Path | None:
    """One of the offered builds by its exact name, or None."""
    if any(p.match(name) for p in _CLIENT_FILES.values()):
        path = settings.data_dir / CLIENT_DIR / name
        return path if path.is_file() else None
    return None
