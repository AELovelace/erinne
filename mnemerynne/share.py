"""Libraries and inboxes stored as folders on the SMB share.

A collection with a `share_path` *is* the folder <share_root>/<share_path>:
folder and file names in the database are the names on disk, and a file's
bytes are read where they lie. The file manager changes the disk first and
the database in the same transaction (fs.py); `sync()` notices what people
change through SMB and brings the database in line.

Access to a share library follows the share's own permissions: Samba keeps
POSIX ACLs in step with the Windows permissions set in Explorer, and this
module evaluates them for the user's linked domain account. Inboxes stay
private to their owner.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import shutil
import sqlite3
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol

from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.share")

MARKER = ".mnemerynne-share"   # present at the share root only while it is mounted
_BAD_CHARS = set('\\/:*?"<>|')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
# Never indexed: Office lock files, Explorer and macOS litter, partial downloads.
_JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
_JUNK_SUFFIXES = (".tmp", ".crdownload", ".part")


class ShareError(OSError):
    """A disk operation on the share failed; the message is safe to show."""


# ------------------------------------------------------------------- runtime
class Checker(Protocol):
    def rights(self, path: Path, uid: int, gids: frozenset[int]) -> tuple[bool, bool]: ...


@dataclass
class Runtime:
    settings: Settings
    directory: object                       # mnemerynne.directory.Directory
    checker: Checker

    @property
    def root(self) -> Path:
        return self.settings.share_root

    @property
    def trash(self) -> Path:
        return self.settings.share_trash_dir


_runtime: Runtime | None = None


def configure(settings: Settings, directory, checker: Checker | None = None) -> Runtime | None:
    """Called once by the web app, the worker and the CLI. No share_root = off."""
    global _runtime
    _runtime = Runtime(settings, directory, checker or PosixChecker()) if settings.share_root else None
    return _runtime


def current() -> Runtime | None:
    return _runtime


def mounted(rt: Runtime) -> bool:
    return (rt.root / MARKER).is_file()


# --------------------------------------------------------------------- names
def check_name(name: str) -> str:
    """Names must also work for Windows users of the share."""
    bad = sorted(set(name) & _BAD_CHARS)
    if bad:
        raise ValueError(f"Names on the share can't contain {' '.join(bad)}.")
    if name.endswith((".", " ")):
        raise ValueError("Names on the share can't end with a dot or a space.")
    if name.split(".")[0].strip().lower() in _RESERVED:
        raise ValueError(f"“{name}” is a reserved name in Windows.")
    return name


def is_junk(name: str) -> bool:
    low = name.lower()
    return (name.startswith((".", "~$")) or low in _JUNK_NAMES or low.endswith(_JUNK_SUFFIXES))


# --------------------------------------------------------------------- paths
def collection_row(conn: sqlite3.Connection, collection_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()


def is_share(conn: sqlite3.Connection, collection_id: int) -> bool:
    row = conn.execute("SELECT share_path FROM collections WHERE id = ?", (collection_id,)).fetchone()
    return bool(row and row["share_path"])


def _rt() -> Runtime:
    if _runtime is None:
        raise ShareError("The share isn't configured on this server (MNEMERYNNE_SHARE_ROOT).")
    return _runtime


def collection_dir(conn: sqlite3.Connection, collection_id: int) -> Path:
    row = collection_row(conn, collection_id)
    return _rt().root / Path(row["share_path"])


def folder_dir(conn: sqlite3.Connection, collection_id: int, folder_id: int | None) -> Path:
    """Where a folder is on disk now: under the share, or inside the trash when
    it (or a folder above it) is in the bin."""
    parts: list[str] = []
    while folder_id is not None:
        row = conn.execute("SELECT name, parent_id, trash_path FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if row["trash_path"]:
            return _rt().trash.joinpath(row["trash_path"], *reversed(parts))
        parts.append(row["name"])
        folder_id = row["parent_id"]
    return collection_dir(conn, collection_id).joinpath(*reversed(parts))


def file_path(conn: sqlite3.Connection, file_id: int) -> Path:
    row = conn.execute("SELECT collection_id, folder_id, display_name, trash_path FROM files WHERE id = ?",
                       (file_id,)).fetchone()
    if row["trash_path"]:
        return _rt().trash / row["trash_path"]
    return folder_dir(conn, row["collection_id"], row["folder_id"]) / row["display_name"]


def disk_stat(path: Path) -> tuple[int, int, int]:
    st = path.stat()
    return st.st_size, st.st_mtime_ns, st.st_ino


def record_stat(conn: sqlite3.Connection, file_id: int, path: Path) -> None:
    size, mtime, inode = disk_stat(path)
    conn.execute("UPDATE files SET disk_size = ?, disk_mtime_ns = ?, disk_inode = ? WHERE id = ?",
                 (size, mtime, inode, file_id))


# --------------------------------------------------------------- disk changes
def _fail(action: str, exc: OSError) -> ShareError:
    log.warning("Share: couldn't %s: %s", action, exc)
    if exc.errno in (errno.EACCES, errno.EPERM):
        return ShareError(f"The server isn't allowed to {action} on the share.")
    if exc.errno == errno.EEXIST or isinstance(exc, FileExistsError):
        return ShareError(f"Couldn't {action}: something with that name already exists on the share.")
    return ShareError(f"Couldn't {action} on the share: {exc.strerror or exc}")


def make_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _fail("create the folder", exc) from exc


def rename(src: Path, dest: Path) -> None:
    """Move or rename within the share and trash (one filesystem). Refuses to
    overwrite, except a change of case only."""
    try:
        if dest.exists() and not (src.parent == dest.parent and src.name.lower() == dest.name.lower()):
            raise FileExistsError(errno.EEXIST, "exists", str(dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dest)
    except OSError as exc:
        raise _fail("move it", exc) from exc


def copy_file(src: Path, dest: Path) -> None:
    try:
        if dest.exists():
            raise FileExistsError(errno.EEXIST, "exists", str(dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    except OSError as exc:
        raise _fail("copy it", exc) from exc


def place(src: Path, dest: Path) -> None:
    """Put a finished upload (a temp file on another disk) at its place on the share."""
    try:
        if dest.exists():
            raise FileExistsError(errno.EEXIST, "exists", str(dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
    except OSError as exc:
        raise _fail("save the file", exc) from exc


def remove(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        raise _fail("delete it", exc) from exc


def trash_name(kind: str, item_id: int, name: str) -> str:
    """Unique name in the trash directory for a binned file ('f') or folder ('d')."""
    return f"{kind}{item_id}-{name}"


# -------------------------------------------------------- POSIX ACL checking
_ACL_USER_OBJ, _ACL_USER, _ACL_GROUP_OBJ, _ACL_GROUP, _ACL_MASK, _ACL_OTHER = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20
_ACL_XATTR = "system.posix_acl_access"
_ACL_DEFAULT_XATTR = "system.posix_acl_default"
_R, _W, _X = 4, 2, 1


def parse_acl(raw: bytes) -> list[tuple[int, int, int]]:
    """The kernel's xattr format: version 2, then (tag u16, perm u16, id u32) entries."""
    if len(raw) < 4 or struct.unpack_from("<I", raw)[0] != 2:
        raise ValueError("Unknown POSIX ACL format")
    return [struct.unpack_from("<HHI", raw, off) for off in range(4, len(raw) - 7, 8)]


def build_acl(entries: Iterable[tuple[int, int, int]]) -> bytes:
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *e) for e in entries)


def allowed(mode: int, owner: int, group: int, acl: list[tuple[int, int, int]] | None,
            uid: int, gids: frozenset[int], want: int) -> bool:
    """POSIX.1e access check (acl(5)) for `want` (r/w/x bits) by uid+gids."""
    if acl is None:
        acl = [(_ACL_USER_OBJ, (mode >> 6) & 7, 0), (_ACL_GROUP_OBJ, (mode >> 3) & 7, 0), (_ACL_OTHER, mode & 7, 0)]
    perms = {tag: perm for tag, perm, _ in acl if tag in (_ACL_USER_OBJ, _ACL_GROUP_OBJ, _ACL_MASK, _ACL_OTHER)}
    mask = perms.get(_ACL_MASK, 7)
    if uid == owner:
        return perms.get(_ACL_USER_OBJ, 0) & want == want
    for tag, perm, eid in acl:
        if tag == _ACL_USER and eid == uid:
            return perm & mask & want == want
    matched = False
    for tag, perm, eid in acl:
        if (tag == _ACL_GROUP_OBJ and group in gids) or (tag == _ACL_GROUP and eid in gids):
            matched = True
            if perm & (mask if _ACL_MASK in perms else 7) & want == want:
                return True
    if matched:
        return False
    return perms.get(_ACL_OTHER, 0) & want == want


class PosixChecker:
    """Reads a folder's owner, mode and access ACL from disk."""

    def rights(self, path: Path, uid: int, gids: frozenset[int]) -> tuple[bool, bool]:
        try:
            st = path.stat()
            try:
                acl = parse_acl(os.getxattr(path, _ACL_XATTR))
            except (OSError, AttributeError, ValueError):
                acl = None   # no ACL (or not Linux): the mode bits decide
        except OSError:
            return False, False
        read = allowed(st.st_mode, st.st_uid, st.st_gid, acl, uid, gids, _R | _X)
        write = allowed(st.st_mode, st.st_uid, st.st_gid, acl, uid, gids, _W | _X)
        return read, write


def make_private(path: Path, uid: int | None) -> None:
    """An inbox folder: only the server and (if linked) its owner's domain
    account; new files and folders inside inherit the same."""
    entries = [(_ACL_USER_OBJ, 7, 0), (_ACL_GROUP_OBJ, 0, 0), (_ACL_MASK, 7, 0), (_ACL_OTHER, 0, 0)]
    if uid is not None:
        entries.insert(1, (_ACL_USER, 7, uid))
    raw = build_acl(sorted(entries))
    try:
        os.setxattr(path, _ACL_XATTR, raw)
        os.setxattr(path, _ACL_DEFAULT_XATTR, raw)
    except (OSError, AttributeError) as exc:
        log.warning("Share: couldn't set the inbox permissions on %s: %s", path, exc)


# ----------------------------------------------------------------- access
def library_reach(conn: sqlite3.Connection, row: sqlite3.Row, user: sqlite3.Row):
    """What `user` may do in a share library, from the share's permissions
    (permissions.Reach), or None for nothing."""
    from mnemerynne.permissions import Reach

    rt = _runtime
    if rt is None or not user["ad_account"]:
        return None
    ids = rt.directory.ids_of(user["ad_account"])
    if ids is None:
        return None
    uid, gids = ids
    base = rt.root / Path(row["share_path"])
    folders = conn.execute("SELECT id, parent_id, name, trash_path FROM folders WHERE collection_id = ?",
                           (row["id"],)).fetchall()
    info = {f["id"]: f for f in folders}
    paths: dict[int, Path | None] = {}

    def path_of(fid: int) -> Path | None:
        # Binned folders take the rights of the place they were binned from.
        if fid not in paths:
            f = info[fid]
            parent = base if f["parent_id"] is None else path_of(f["parent_id"])
            paths[fid] = None if parent is None else parent / f["name"]
        return paths[fid]

    root_read, root_write = rt.checker.rights(base, uid, gids)
    claude_on = bool(row["claude_enabled"])
    reach = Reach(row["id"], "library", row["name"], root_read, root_write, claude_on and root_read,
                  parents={f["id"]: f["parent_id"] for f in folders})
    for fid in info:
        path = path_of(fid)
        if path is None:
            continue
        read, write = rt.checker.rights(path, uid, gids) if path.exists() else (root_read, root_write)
        if read:
            reach.read.add(fid)
            if claude_on:
                reach.claude.add(fid)
        if write:
            reach.write.add(fid)
    if not (reach.root_read or reach.read):
        return None
    return reach


# ------------------------------------------------------------------- inboxes
def attach_inbox(conn: sqlite3.Connection, collection_id: int, username: str, ad_account: str | None = None) -> None:
    """Give a new (empty) inbox its folder under Users/ on the share."""
    rt = _runtime
    if rt is None:
        return
    if not mounted(rt):
        # Never create folders on the system disk under an empty mount point;
        # `mnemerynne share-init` moves the (still empty) inbox later.
        log.warning("Share: %s isn't mounted; %s's inbox stays inside Mnemerynne for now.", rt.root, username)
        return
    share_path = f"{rt.settings.users_folder}/{username}"
    path = rt.root / share_path
    make_dir(path)
    conn.execute("UPDATE collections SET share_path = ?, share_inode = ? WHERE id = ?",
                 (share_path, path.stat().st_ino, collection_id))
    secure_inbox(path, ad_account)


def secure_inbox(path: Path, ad_account: str | None) -> None:
    rt = _runtime
    if rt is None:
        return
    ids = rt.directory.ids_of(ad_account) if ad_account else None
    make_private(path, ids[0] if ids else None)


# ---------------------------------------------------------------------- sync
@dataclass
class Stats:
    libraries: int = 0
    added: int = 0
    changed: int = 0
    moved: int = 0
    removed: int = 0
    folders_added: int = 0
    folders_removed: int = 0
    skipped: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return any((self.libraries, self.added, self.changed, self.moved, self.removed,
                    self.folders_added, self.folders_removed))


@dataclass
class _DiskFile:
    size: int
    mtime_ns: int
    inode: int


def _walk(base: Path) -> tuple[dict[tuple[str, ...], int], dict[tuple[str, ...], _DiskFile]]:
    """(folders, files) under base, as path tuples; junk and hidden names skipped."""
    folders: dict[tuple[str, ...], int] = {}
    files: dict[tuple[str, ...], _DiskFile] = {}
    stack = [()]
    while stack:
        rel = stack.pop()
        try:
            entries = list(os.scandir(base.joinpath(*rel)))
        except OSError as exc:
            log.warning("Share: can't read %s: %s", base.joinpath(*rel), exc)
            continue
        for entry in entries:
            if is_junk(entry.name):
                continue
            try:
                if entry.is_symlink():
                    continue
                path = rel + (entry.name,)
                if entry.is_dir():
                    folders[path] = entry.inode()
                    stack.append(path)
                elif entry.is_file():
                    st = entry.stat()
                    files[path] = _DiskFile(st.st_size, st.st_mtime_ns, entry.inode())
            except OSError:
                continue
    return folders, files


def _db_tree(conn: sqlite3.Connection, collection_id: int) -> tuple[dict[tuple[str, ...], int], dict[tuple[str, ...], sqlite3.Row]]:
    """Live folders and files of a collection, by path."""
    from mnemerynne import fs

    live = fs.live_folder_ids(conn, collection_id)
    rows = {r["id"]: r for r in conn.execute("SELECT id, parent_id, name FROM folders WHERE collection_id = ?",
                                              (collection_id,))}
    paths: dict[int, tuple[str, ...]] = {}

    def path_of(fid: int) -> tuple[str, ...]:
        if fid not in paths:
            r = rows[fid]
            paths[fid] = (path_of(r["parent_id"]) if r["parent_id"] is not None else ()) + (r["name"],)
        return paths[fid]

    folders = {path_of(fid): fid for fid in live}
    files = {}
    for f in conn.execute("SELECT id, folder_id, display_name, blob_sha256, disk_size, disk_mtime_ns, disk_inode "
                          "FROM files WHERE collection_id = ? AND deleted_at IS NULL", (collection_id,)):
        if f["folder_id"] is None or f["folder_id"] in live:
            files[(path_of(f["folder_id"]) if f["folder_id"] is not None else ()) + (f["display_name"],)] = f
    return folders, files


def _discover(conn: sqlite3.Connection, rt: Runtime, stats: Stats) -> None:
    """Top-level folders become libraries; Users/<name> attaches to empty inboxes."""
    from mnemerynne import permissions

    users_dir = rt.settings.users_folder.lower()
    known = {r["share_path"].lower(): r for r in conn.execute(
        "SELECT * FROM collections WHERE share_path IS NOT NULL")}
    by_inode = {r["share_inode"]: r for r in known.values() if r["share_inode"]}
    for entry in sorted(os.scandir(rt.root), key=lambda e: e.name.lower()):
        if is_junk(entry.name) or not entry.is_dir() or entry.is_symlink():
            continue
        if entry.name.lower() == users_dir:
            for sub in os.scandir(entry.path):
                if is_junk(sub.name) or not sub.is_dir():
                    continue
                share_path = f"{rt.settings.users_folder}/{sub.name}"
                if share_path.lower() in known:
                    continue
                inbox = conn.execute(
                    "SELECT c.id FROM collections c JOIN users u ON u.id = c.owner_user_id "
                    "WHERE c.kind = 'private' AND c.share_path IS NULL AND u.username = ? COLLATE NOCASE "
                    "AND NOT EXISTS (SELECT 1 FROM files WHERE collection_id = c.id)", (sub.name,)).fetchone()
                if inbox:
                    conn.execute("UPDATE collections SET share_path = ?, share_inode = ? WHERE id = ?",
                                 (share_path, sub.inode(), inbox["id"]))
            continue
        if entry.name.lower() in known:
            continue
        renamed = by_inode.get(entry.inode())
        if renamed is not None and renamed["kind"] == "library" and not (rt.root / renamed["share_path"]).exists():
            conn.execute("UPDATE collections SET share_path = ?, name = ? WHERE id = ?",
                         (entry.name, entry.name, renamed["id"]))
            log.info("Share: library %s was renamed to %s on the share.", renamed["name"], entry.name)
            continue
        if permissions.get_library(conn, entry.name):
            stats.skipped.append(f"{entry.name}: a library with that name isn't on the share yet")
            continue
        library_id = permissions.create_library(conn, entry.name)
        conn.execute("UPDATE collections SET share_path = ?, share_inode = ? WHERE id = ?",
                     (entry.name, entry.inode(), library_id))
        stats.libraries += 1
        log.info("Share: new library %s.", entry.name)


def hash_file(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(1024 * 1024):
            sha.update(block)
    return sha.hexdigest()


def sync(conn: sqlite3.Connection, settings: Settings | None = None) -> Stats:
    """Bring the database in line with the share. Safe to run while people use
    the file manager: every change is checked again under the write lock."""
    from mnemerynne import fs, jobs
    from mnemerynne.db import transaction
    from mnemerynne.ingest import store

    stats = Stats()
    rt = _runtime
    if rt is None:
        return stats
    settings = settings or rt.settings
    if not mounted(rt):
        log.warning("Share: %s has no %s file (not mounted?); nothing synced.", rt.root, MARKER)
        stats.skipped.append(f"{rt.root} isn't mounted (no {MARKER})")
        return stats
    with transaction(conn):
        _discover(conn, rt, stats)
    settle_before = time.time_ns() - settings.share_settle_seconds * 1_000_000_000

    # Pass 1, no lock: what changed, and the hashes of new and changed files.
    plans = []
    for c in conn.execute("SELECT id, share_path FROM collections WHERE share_path IS NOT NULL").fetchall():
        base = rt.root / c["share_path"]
        if not base.is_dir():
            continue
        disk_folders, disk_files = _walk(base)
        db_folders, db_files = _db_tree(conn, c["id"])
        plans.append((c["id"], base, disk_folders, disk_files, db_folders, db_files))

    gone: dict[int, tuple[int, tuple[str, ...], sqlite3.Row]] = {}   # inode -> (collection, path, row)
    for cid, base, _, disk_files, _, db_files in plans:
        for path, row in db_files.items():
            if path not in disk_files and row["disk_inode"]:
                gone[row["disk_inode"]] = (cid, path, row)

    hashes: dict[tuple[int, tuple[str, ...]], str] = {}
    for cid, base, _, disk_files, _, db_files in plans:
        for path, f in disk_files.items():
            if f.mtime_ns > settle_before:
                continue   # still being written; next scan
            row = db_files.get(path)
            if row is not None and (row["disk_size"], row["disk_mtime_ns"]) == (f.size, f.mtime_ns):
                continue
            if row is None and f.inode in gone and gone[f.inode][2]["disk_size"] == f.size:
                continue   # a move: no need to read it
            try:
                hashes[(cid, path)] = hash_file(base.joinpath(*path))
            except OSError as exc:
                stats.skipped.append(f"{'/'.join(path)}: {exc}")

    # Pass 2, per collection under the write lock: re-check, then apply.
    for cid, base, disk_folders, disk_files, _, _ in plans:
        with transaction(conn):
            db_folders, db_files = _db_tree(conn, cid)
            folder_ids = dict(db_folders)
            for path in sorted(disk_folders, key=len):
                if path not in folder_ids and base.joinpath(*path).is_dir():
                    try:
                        check_name(path[-1])
                    except ValueError:
                        stats.skipped.append(f"{'/'.join(path)}: name not allowed")
                        continue
                    parent = folder_ids.get(path[:-1]) if len(path) > 1 else None
                    if len(path) > 1 and parent is None:
                        continue
                    folder_ids[path] = fs.ensure_path(conn, cid, parent, [path[-1]], None)
                    stats.folders_added += 1

            for path, f in disk_files.items():
                full = base.joinpath(*path)
                folder_id = folder_ids.get(path[:-1]) if len(path) > 1 else None
                if len(path) > 1 and folder_id is None:
                    continue
                row = db_files.get(path)
                if row is not None:
                    sha = hashes.get((cid, path))
                    if sha is None or not full.is_file() or disk_stat(full)[:2] != (f.size, f.mtime_ns):
                        continue
                    if sha != row["blob_sha256"]:
                        note = _new_blob(conn, settings, full, sha)
                        status = "stored" if _blob_kind(conn, sha) == "other" else "queued"
                        conn.execute("UPDATE files SET blob_sha256 = ?, status = ?, error = NULL, progress = NULL, "
                                     "notes = ?, updated_at = datetime('now') WHERE id = ?",
                                     (sha, status, note, row["id"]))
                        if status == "queued":
                            jobs.enqueue(conn, "ingest", row["id"])
                        _drop_unused_blob(conn, settings, row["blob_sha256"])
                        stats.changed += 1
                    record_stat(conn, row["id"], full)
                    continue
                if not full.is_file() or disk_stat(full)[2] != f.inode:
                    continue
                moved = gone.get(f.inode)
                if moved is not None:
                    old_cid, old_path, old_row = moved
                    current = conn.execute("SELECT * FROM files WHERE id = ? AND deleted_at IS NULL",
                                           (old_row["id"],)).fetchone()
                    old_base = rt.root / conn.execute("SELECT share_path FROM collections WHERE id = ?",
                                                      (old_cid,)).fetchone()["share_path"]
                    if current is not None and not old_base.joinpath(*old_path).exists():
                        conn.execute("UPDATE files SET collection_id = ?, folder_id = ?, display_name = ?, "
                                     "updated_at = datetime('now') WHERE id = ?",
                                     (cid, folder_id, path[-1], old_row["id"]))
                        record_stat(conn, old_row["id"], full)
                        fs._sync_scope(conn, [old_row["id"]])
                        del gone[f.inode]
                        stats.moved += 1
                        continue
                sha = hashes.get((cid, path))
                if sha is None:
                    continue
                if fs._taken(conn, cid, folder_id, path[-1], "file"):
                    stats.skipped.append(f"{'/'.join(path)}: another file differs only in upper/lower case")
                    continue
                note = _new_blob(conn, settings, full, sha)
                kind = _blob_kind(conn, sha)
                file_id = conn.execute(
                    "INSERT INTO files(collection_id, folder_id, blob_sha256, display_name, status, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (cid, folder_id, sha, path[-1], "stored" if kind == "other" else "queued", note)).lastrowid
                record_stat(conn, file_id, full)
                if kind != "other":
                    jobs.enqueue(conn, "ingest", file_id)
                stats.added += 1
        # Deletions after every collection's additions, so moves between
        # collections are recognised first.
    for cid, base, disk_folders, _, _, _ in plans:
        with transaction(conn):
            db_folders, db_files = _db_tree(conn, cid)
            for path, row in db_files.items():
                if not base.joinpath(*path).exists():
                    store.delete_record(conn, settings, row["id"])
                    stats.removed += 1
            for path, fid in sorted(db_folders.items(), key=lambda kv: -len(kv[0])):
                if not base.joinpath(*path).is_dir():
                    if conn.execute("SELECT 1 FROM files WHERE folder_id = ? AND deleted_at IS NULL", (fid,)).fetchone():
                        continue
                    conn.execute("UPDATE files SET folder_id = NULL WHERE folder_id = ?", (fid,))  # binned ones
                    conn.execute("UPDATE folders SET parent_id = NULL WHERE parent_id = ? AND deleted_at IS NOT NULL",
                                 (fid,))
                    conn.execute("DELETE FROM folders WHERE id = ?", (fid,))
                    stats.folders_removed += 1
    # Libraries whose folder is gone from the share (the share is mounted).
    with transaction(conn):
        for c in conn.execute("SELECT id, name, share_path FROM collections WHERE kind = 'library' "
                              "AND share_path IS NOT NULL").fetchall():
            if not (rt.root / c["share_path"]).is_dir():
                for f in conn.execute("SELECT id FROM files WHERE collection_id = ?", (c["id"],)).fetchall():
                    store.delete_record(conn, settings, f["id"])
                conn.execute("DELETE FROM collections WHERE id = ?", (c["id"],))
                log.info("Share: library %s is gone from the share; removed.", c["name"])
                stats.libraries += 1
    if stats:
        log.info("Share sync: %s", stats)
    return stats


def _new_blob(conn: sqlite3.Connection, settings: Settings, path: Path, sha: str) -> str | None:
    """A blob row for content read from the share (the bytes stay there).
    Returns why it won't be searchable, if the blob is new and that applies."""
    from mnemerynne.ingest import store

    if conn.execute("SELECT 1 FROM blobs WHERE sha256 = ?", (sha,)).fetchone():
        return None
    kind, mime, size, note = store.inspect(path, path.name, settings)
    conn.execute("INSERT INTO blobs(sha256, kind, mime, size, rel_path) VALUES (?, ?, ?, ?, ?)",
                 (sha, kind, mime, size, store.blob_rel_path(sha, kind)))
    return note


def _blob_kind(conn: sqlite3.Connection, sha: str) -> str:
    return conn.execute("SELECT kind FROM blobs WHERE sha256 = ?", (sha,)).fetchone()["kind"]


def _drop_unused_blob(conn: sqlite3.Connection, settings: Settings, sha: str) -> None:
    from mnemerynne.ingest import store

    if conn.execute("SELECT 1 FROM files WHERE blob_sha256 = ? LIMIT 1", (sha,)).fetchone():
        return
    row = conn.execute("SELECT rel_path, ocr_rel_path FROM blobs WHERE sha256 = ?", (sha,)).fetchone()
    conn.execute("DELETE FROM blobs WHERE sha256 = ?", (sha,))
    if row:
        for rel in (row["rel_path"], row["ocr_rel_path"]):
            if rel:
                store.blob_path(settings, rel).unlink(missing_ok=True)
