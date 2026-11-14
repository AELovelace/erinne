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
import json
import logging
import os
import queue
import re
import secrets
import shutil
import sqlite3
import stat as stat_mode
import struct
import subprocess
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterable, Protocol

from mnemerynne import db, events
from mnemerynne.config import Settings

log = logging.getLogger("mnemerynne.share")

MARKER = ".mnemerynne-share"   # present at the share root only while it is mounted
STAGE_PREFIX = ".mnemerynne-upload-"   # an upload on its way in, hidden in the folder it will land in
STAGE_MAX_AGE = 86400          # one untouched for this long was left by a crash, and the scan clears it
_BAD_CHARS = set('\\/:*?"<>|')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
# Never indexed: Office lock files, Explorer and macOS litter, partial downloads.
_JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
_JUNK_SUFFIXES = (".tmp", ".crdownload", ".part")


class ShareError(OSError):
    """A disk operation on the share failed; the message is safe to show."""


class ShareUnavailable(ShareError):
    """The share isn't mounted (or configured) right now: try again later."""


# ------------------------------------------------------------------- runtime
class Checker(Protocol):
    def rights(self, path: Path, uid: int, gids: frozenset[int]) -> tuple[bool, bool]: ...


@dataclass
class Runtime:
    settings: Settings
    directory: object                       # mnemerynne.directory.Directory
    checker: Checker
    rights: dict = field(default_factory=dict, repr=False)      # (library, uid, gids) -> _Rights, read from disk
    rights_walks: dict = field(default_factory=dict, repr=False)   # the same key -> the lock its walk holds
    # A big library's first look, answered while its first walk runs: what's been read so far (library_reach).
    provisional: dict = field(default_factory=dict, repr=False)    # the same key -> _Rights (partial)
    watching: bool = False                  # the share watcher is up: walks only as a safety net (_due_after)
    stopping: bool = False                  # the app is shutting down: the watcher stops for good
    watch_proc: object = field(default=None, repr=False)          # the inotifywait process, while it runs
    watcher: object = field(default=None, repr=False)             # the thread that runs it
    # Whether this process walks the share's permissions (Phase 5). False (settings.share_walks == "service", and
    # not the share service itself): it reads the share service's walks from share-rights.db instead.
    owner: bool = True
    loaded: dict = field(default_factory=dict, repr=False)   # key -> when its rights were read from share-rights.db
    asked: dict = field(default_factory=dict, repr=False)    # key -> when the share service was asked to walk it

    @property
    def root(self) -> Path:
        return self.settings.share_root

    @property
    def trash(self) -> Path:
        return self.settings.share_trash_dir


_runtime: Runtime | None = None


def configure(settings: Settings, directory, checker: Checker | None = None, *,
              owner: bool | None = None) -> Runtime | None:
    """Called once by the web app, the worker, the CLI and each service. No share_root = off. `owner`: whether this
    process walks the share's permissions; by default unless settings.share_walks is "service" (the share service
    passes True)."""
    global _runtime
    _runtime = Runtime(settings, directory, checker or PosixChecker()) if settings.share_root else None
    if _runtime is not None:
        _runtime.owner = owner if owner is not None else settings.share_walks != "service"
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
    """The share, for a disk operation: configured and mounted. Never write
    into an empty mount point (the files would hide on the system disk)."""
    if _runtime is None:
        raise ShareUnavailable("The share isn't configured on this server (MNEMERYNNE_SHARE_ROOT).")
    if not mounted(_runtime):
        raise ShareUnavailable("The file share isn't available right now; try again later.")
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
        return ShareError(f"The server isn't allowed to {action} on the share: the mnemerynne service user needs "
                          f"Modify rights on that folder (its entry shows as “Unix User\\mnemerynne” in the folder's "
                          f"Security tab in Windows).")
    if exc.errno == errno.EEXIST or isinstance(exc, FileExistsError):
        return ShareError(f"Couldn't {action}: something with that name already exists on the share.")
    return ShareError(f"Couldn't {action} on the share: {exc.strerror or exc}")


def make_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _fail("create the folder", exc) from exc


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def rename(src: Path, dest: Path, action: str = "move it") -> None:
    """Move or rename within the share and trash (one filesystem). Never
    replaces anything: a file goes by link + unlink, which fails if the
    destination appeared meanwhile; only the same file under another case
    (case-insensitive filesystems) is renamed in place. action: how an error
    describes this."""
    try:
        if dest.exists() and not _same(src, dest):
            raise FileExistsError(errno.EEXIST, "exists", str(dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_file() and not _same(src, dest):
            try:
                os.link(src, dest)
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    raise
                os.rename(src, dest)   # no hard links here (e.g. Windows tests)
            else:
                os.unlink(src)
        else:
            # Folders: rename(2) refuses a non-empty target; an empty one that
            # appeared since the check above is all it could replace.
            os.rename(src, dest)
    except OSError as exc:
        raise _fail(action, exc) from exc


def copy_bytes(src: Path, dest: Path) -> None:
    """Copy the contents and the modified time into a file that must not exist
    yet (O_EXCL: never overwrites). Nothing else comes along: no mode bits,
    POSIX ACLs or SELinux label (shutil.copy2/copystat would copy those
    xattrs), so the copy inherits its permissions and label from where it lands."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as fin, open(dest, "xb") as fout:
        shutil.copyfileobj(fin, fout, 1024 * 1024)
    st = src.stat()
    os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns))


_copy_new = copy_bytes


def copy_file(src: Path, dest: Path) -> None:
    try:
        _copy_new(src, dest)
    except OSError as exc:
        raise _fail("copy it", exc) from exc


# Uploads reach the share in two steps, so that a huge file never holds the database. Its bytes are written first, with
# no transaction open, to a hidden file in the folder it will land in; created there, it gets that folder's permissions
# and SELinux label. Then, inside the transaction, rename() gives it its real name, which takes no time however big it
# is. A temp file from elsewhere can't simply be renamed in: it would keep the private permissions it was made with.
def _stage_path(folder: Path) -> Path:
    return folder / f"{STAGE_PREFIX}{secrets.token_hex(8)}"


def open_stage(folder: Path) -> tuple[Path, BinaryIO]:
    """A new hidden file in `folder` to write an upload into: (its path, the open file)."""
    path = _stage_path(folder)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        return path, open(path, "xb")
    except OSError as exc:
        raise _fail("save the file", exc) from exc


def stage(src: Path, folder: Path) -> Path:
    """Copy a finished temp file to a hidden file in `folder`, and remove the temp file. Returns the hidden file's path.
    Call this with no transaction open."""
    path = _stage_path(folder)
    try:
        _copy_new(src, path)
    except OSError as exc:
        path.unlink(missing_ok=True)                      # never leave half a file behind
        raise _fail("save the file", exc) from exc
    src.unlink(missing_ok=True)
    return path


def _clear_stale_stage(entry: os.DirEntry) -> None:
    """Delete an upload a crash left hidden on the share. ctime as well as mtime, because a backed-up file carries the
    laptop's old modified time from the moment it is staged."""
    if not entry.name.startswith(STAGE_PREFIX):
        return
    try:
        st = entry.stat(follow_symlinks=False)
        if time.time() - max(st.st_mtime, st.st_ctime) > STAGE_MAX_AGE:
            os.unlink(entry.path)
            log.info("Share: removed an abandoned upload: %s", entry.path)
    except OSError:                                       # gone already, or not ours to delete: the next scan looks again
        pass


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
            if not stat_mode.S_ISDIR(st.st_mode):
                return False, False   # not a folder (any more): nobody gets in until a scan sorts it out
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
    account; new files and folders inside inherit the same. The server gets a
    named entry too: files its owner adds through SMB are owned by them, and
    the server must still read them."""
    entries = [(_ACL_USER_OBJ, 7, 0), (_ACL_GROUP_OBJ, 0, 0), (_ACL_MASK, 7, 0), (_ACL_OTHER, 0, 0)]
    service = os.getuid() if hasattr(os, "getuid") else None
    for who in {uid, service} - {None}:
        entries.append((_ACL_USER, 7, who))
    # The kernel wants entries ordered by tag, then id.
    raw = build_acl(sorted(entries, key=lambda e: (e[0], e[2])))
    try:
        os.setxattr(path, _ACL_XATTR, raw)
        os.setxattr(path, _ACL_DEFAULT_XATTR, raw)
    except (OSError, AttributeError) as exc:
        log.warning("Share: couldn't set the inbox permissions on %s: %s", path, exc)


# ----------------------------------------------------------------- access
@dataclass
class _Rights:
    """One person's permissions in one library, as last read from the share."""
    at: float                                       # when the walk that filled it began (time.monotonic)
    folders: dict[int | None, tuple[bool, bool]]    # folder id (None: the library's top) -> (read, write)


def forget_rights() -> None:
    """Forget every cached permission, saved ones too: the next list reads them from the share again."""
    if _runtime is not None:
        _runtime.rights.clear()
        _runtime.loaded.clear()
        _forget_saved(_runtime)                    # or a restart would bring back what was forgotten here
        _announce(None)                            # the other processes forget theirs too


# ------------------------------------------------------------ saved permissions
# Each walk is also kept in a file of its own (data_dir/share-rights.db), so the first look after a restart is
# answered from the last walk instead of walking the share again (28,789 folders' permissions read from the disk
# on the box), while a fresh walk runs behind it. A separate file, so saving never waits on or holds the main
# database's write lock. It's a cache: missing or damaged, it only means a walk, as before; nothing else reads it.
_SAVED_DAYS = 30                                   # a saved walk nobody has looked at for this long is dropped
LAZY_FROM = 2000           # libraries with more folders than this answer a first look before their walk finishes
WATCHED_WALK_SECONDS = 6 * 3600   # with the share watcher up, a whole walk still runs this often (a safety net)


def _saved_path(rt: Runtime) -> Path:
    return Path(rt.settings.data_dir) / "share-rights.db"


def _saved_db(rt: Runtime) -> sqlite3.Connection:
    """The cache file, made on first use. Plain sqlite3: it needs none of the main database's extensions."""
    path = _saved_path(rt)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5, isolation_level=None)   # the web app, worker and CLI may share it
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError as exc:
        if "not a database" not in str(exc) and "malformed" not in str(exc):
            conn.close()
            raise                                  # locked or the like: try again another time
        conn.close()                               # damaged: it's only a cache, so start a new one
        log.warning("Share: %s was damaged (%s); starting it afresh", path, exc)
        for old in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            old.unlink(missing_ok=True)
        conn = sqlite3.connect(path, timeout=5, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS rights ("
                 " library INTEGER NOT NULL, uid INTEGER NOT NULL, gids TEXT NOT NULL,"   # whose walk, of what
                 " walked_at REAL NOT NULL,"                                              # when it began (Unix time)
                 " folders TEXT NOT NULL,"                       # JSON {folder id or "top": read 1 | write 2}
                 " PRIMARY KEY (library, uid, gids))")
    return conn


def _key_columns(key: tuple) -> tuple[int, int, str]:
    """(library, uid, gids) as the table keeps them: the groups as a sorted list, so the same set always matches."""
    library, uid, gids = key
    return int(library), int(uid), ",".join(str(g) for g in sorted(gids))


def _save_rights(rt: Runtime, key: tuple, rights: _Rights) -> None:
    """Keep this walk on disk. Never fails the walk it came from: the file is only a cache."""
    try:
        walked_at = time.time() - (time.monotonic() - rights.at)    # the walk's start, on the wall clock
        folders = json.dumps({("top" if fid is None else str(fid)): int(read) | int(write) << 1
                              for fid, (read, write) in list(rights.folders.items())},   # a copy: lists may correct it
                             separators=(",", ":"))
        conn = _saved_db(rt)
        try:
            conn.execute("INSERT OR REPLACE INTO rights VALUES (?, ?, ?, ?, ?)",
                         (*_key_columns(key), walked_at, folders))
            conn.execute("DELETE FROM rights WHERE walked_at < ?", (time.time() - _SAVED_DAYS * 86400,))
        finally:
            conn.close()
    except Exception as exc:                       # a full disk, a locked or damaged file: just not saved this time
        log.warning("Share: couldn't save permissions to %s: %s", _saved_path(rt), exc)


def _load_rights(rt: Runtime, key: tuple) -> _Rights | None:
    """The walk saved for `key` before the app (re)started, or None. Its age carries over, so a walk is due as usual."""
    if not _saved_path(rt).exists():
        return None                                # nothing saved yet: don't make the file just to look
    try:
        conn = _saved_db(rt)
        try:
            row = conn.execute("SELECT walked_at, folders FROM rights WHERE library = ? AND uid = ? AND gids = ?",
                               _key_columns(key)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        folders = {(None if fid == "top" else int(fid)): (bool(bits & 1), bool(bits & 2))
                   for fid, bits in json.loads(row[1]).items()}
    except Exception as exc:                       # damaged: walk the share instead
        log.warning("Share: couldn't read saved permissions from %s: %s", _saved_path(rt), exc)
        return None
    if None not in folders:
        return None                                # no entry for the library's top: not a whole walk, don't use it
    age = max(0.0, time.time() - row[0])
    return _Rights(time.monotonic() - age, folders)


def _forget_saved(rt: Runtime) -> None:
    if not _saved_path(rt).exists():
        return
    try:
        conn = _saved_db(rt)
        try:
            conn.execute("DELETE FROM rights")
        finally:
            conn.close()
    except Exception as exc:
        log.warning("Share: couldn't clear saved permissions in %s: %s", _saved_path(rt), exc)


def _saved_keys(rt: Runtime, library_id: int) -> set[tuple]:
    """Every (library, uid, gids) with a walk saved for this library."""
    if not _saved_path(rt).exists():
        return set()
    try:
        conn = _saved_db(rt)
        try:
            rows = conn.execute("SELECT uid, gids FROM rights WHERE library = ?", (library_id,)).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        log.warning("Share: couldn't list saved permissions in %s: %s", _saved_path(rt), exc)
        return set()
    return {(library_id, uid, frozenset(int(g) for g in gids.split(",") if g)) for uid, gids in rows}


# ------------------------------------------------------------- watching the share
# The web app watches the share for permission changes (inotify, through inotifywait, as share-acl-watch.sh
# does): when a folder's permissions change, on the box or from Windows over SMB, just that folder is read again,
# for everyone with a walk kept (in memory and saved). Lists then follow the share within seconds, and a whole
# walk only runs now and then (WATCHED_WALK_SECONDS) as a safety net. Without the watcher (no inotifywait, not
# Linux, too many folders to watch), walks are due every share_rights_seconds, as before.
_BATCH_SECONDS = 2.0      # gather the events this long, then apply them together (a setfacl -R sends thousands)
_BATCH_LIMIT = 2000       # more changed folders than this in a library at once: a whole walk instead


def start_watching() -> None:
    """Called once by the web app at start. Quietly does nothing where there's no share or no inotifywait."""
    rt = _runtime
    if rt is None or not rt.owner or rt.watcher is not None or shutil.which("inotifywait") is None:
        return                                     # not the owner: the share service watches (Phase 5)
    rt.stopping = False
    rt.watcher = threading.Thread(target=_watch_forever, args=(rt,), name="share-watch", daemon=True)
    rt.watcher.start()


def stop_watching() -> None:
    """Called by the web app on shutdown."""
    rt = _runtime
    if rt is None:
        return
    rt.stopping = True
    proc = rt.watch_proc
    if proc is not None and proc.poll() is None:
        proc.terminate()


def _watch_forever(rt: Runtime) -> None:
    """Keep inotifywait running; if it stops (the share unmounted, a watch limit), walks go back to their timer
    and it's tried again later, a little less often each time."""
    delay = 5
    while not rt.stopping:
        began = time.monotonic()
        try:
            _watch_once(rt)
        except Exception:
            log.exception("Share: the watcher stopped")
        rt.watching = False                        # from now until it's back: timed walks, as without it
        if rt.stopping:
            return
        delay = 5 if time.monotonic() - began > 600 else min(delay * 2, 600)
        time.sleep(delay)


def _watch_once(rt: Runtime) -> None:
    """One run of inotifywait over the share: folders whose permissions change (ATTRIB), hidden paths left out."""
    if not mounted(rt):
        return
    proc = subprocess.Popen(
        ["inotifywait", "-m", "-r", "-e", "attrib", "--exclude", r"/\.", "--format", "%e\t%w%f", str(rt.root)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    rt.watch_proc = proc

    def setup() -> None:
        """inotifywait says on stderr when every folder is watched, or why it couldn't (the watch limit)."""
        for line in proc.stderr:
            if "Watches established" in line:
                rt.watching = True
                log.info("Share: watching %s for permission changes", rt.root)
            elif line.strip() and "Setting up watches" not in line:
                log.warning("Share watcher: %s", line.strip())

    threading.Thread(target=setup, name="share-watch-setup", daemon=True).start()
    try:
        _watch_events(rt, proc.stdout)
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait()
        rt.watch_proc = None


def _watch_events(rt: Runtime, lines) -> None:
    """Apply the watcher's lines in batches: everything that arrives within _BATCH_SECONDS of the first goes
    together, so a recursive permission change is one pass, not thousands."""
    waiting: queue.Queue = queue.Queue()

    def read() -> None:
        for line in lines:
            waiting.put(line)
        waiting.put(None)                          # the watcher stopped

    threading.Thread(target=read, name="share-watch-read", daemon=True).start()
    while True:
        line = waiting.get()
        if line is None:
            return
        batch, ended = [line], False
        until = time.monotonic() + _BATCH_SECONDS
        while (left := until - time.monotonic()) > 0:
            try:
                more = waiting.get(timeout=left)
            except queue.Empty:
                break
            if more is None:
                ended = True
                break
            batch.append(more)
        try:
            apply_events(rt, batch)
        except Exception:
            log.exception("Share: couldn't apply permission changes")
        if ended:
            return


def apply_events(rt: Runtime, lines: list[str]) -> None:
    """A batch of the watcher's lines ("EVENTS<tab>path"): folders whose permissions changed are read again; an
    overflowed queue (the kernel dropped events) means nothing can be trusted, so every walk is due."""
    changed: set[Path] = set()
    for line in lines:
        events, _, path = line.rstrip("\n").partition("\t")
        if "Q_OVERFLOW" in events:
            _mark_due(rt, None)
            return
        if "ISDIR" in events and path:             # a file's own permissions don't change what a folder allows
            changed.add(Path(path))
    if not changed:
        return
    conn = db.connect(rt.settings.db_path)
    try:
        apply_changes(rt, conn, changed)
    finally:
        conn.close()


def apply_changes(rt: Runtime, conn: sqlite3.Connection, paths: Iterable[Path]) -> int:
    """These folders' permissions changed on the share: read just them again, for every walk kept of their
    library (in memory, saved, and first looks still filling in). A library with more than _BATCH_LIMIT changes
    at once is marked due for a whole walk instead. Returns how many folders it found in a library."""
    libraries = sorted(((r["id"], Path(r["share_path"]).parts) for r in conn.execute(
        "SELECT id, share_path FROM collections WHERE kind = 'library' AND share_path IS NOT NULL")),
        key=lambda lib: -len(lib[1]))              # the deepest match wins, if one library sits inside another
    changed: dict[int, dict[int | None, Path]] = {}
    for path in paths:
        try:
            rel = Path(path).relative_to(rt.root).parts
        except ValueError:
            continue                               # not on the share (the trash, say)
        for library_id, top in libraries:
            if rel[:len(top)] == top:
                folder_id = _folder_at(conn, library_id, rel[len(top):])
                if folder_id is not False:         # a folder the scan hasn't added yet is read when first seen
                    changed.setdefault(library_id, {})[folder_id] = Path(path)
                break
    for library_id, folders in changed.items():
        if len(folders) > _BATCH_LIMIT:
            _mark_due(rt, library_id)
        else:
            _recheck_folders(rt, library_id, folders)
    return sum(len(folders) for folders in changed.values())


def _folder_at(conn: sqlite3.Connection, library_id: int, parts: tuple[str, ...]) -> int | None | bool:
    """The live folder at this path inside a library: its id, None for the library's top, False if unknown."""
    parent = None
    for name in parts:
        row = conn.execute("SELECT id FROM folders WHERE collection_id = ? AND parent_id IS ? AND name = ? "
                           "AND trash_path IS NULL", (library_id, parent, name)).fetchone()
        if row is None:
            return False
        parent = row[0]
    return parent


def _recheck_folders(rt: Runtime, library_id: int, folders: dict[int | None, Path]) -> None:
    """Read these folders' permissions again for everyone with a walk of this library, and save the result."""
    def update(key: tuple, rights: _Rights) -> None:
        _, uid, gids = key
        for folder_id, path in folders.items():
            try:
                rights.folders[folder_id] = rt.checker.rights(path, uid, gids)
            except OSError:
                rights.folders[folder_id] = (False, False)   # gone or unreadable now: closed until the next walk

    in_memory = {key for key in list(rt.rights) if key[0] == library_id}
    for key in in_memory:
        if (rights := rt.rights.get(key)) is not None:
            update(key, rights)
            _save_rights(rt, key, rights)
    for key in _saved_keys(rt, library_id) - in_memory:   # people not here since the restart: keep theirs current
        if (rights := _load_rights(rt, key)) is not None:
            update(key, rights)
            _save_rights(rt, key, rights)
    for key in [k for k in list(rt.provisional) if k[0] == library_id]:
        if (rights := rt.provisional.get(key)) is not None:
            update(key, rights)                    # first looks still filling in: theirs too (read just now)
    _announce(library_id)                          # the processes that read the share service's walks reload them


def _mark_due(rt: Runtime, library_id: int | None) -> None:
    """Every walk of this library (None: of every library) is due now: the next look starts a whole walk."""
    for key in list(rt.rights):
        if library_id is None or key[0] == library_id:
            if (rights := rt.rights.get(key)) is not None:
                rights.at -= 10 ** 9               # far in the past
    if not _saved_path(rt).exists():
        return
    try:
        conn = _saved_db(rt)
        try:
            if library_id is None:
                conn.execute("UPDATE rights SET walked_at = 0")
            else:
                conn.execute("UPDATE rights SET walked_at = 0 WHERE library = ?", (library_id,))
        finally:
            conn.close()
    except Exception as exc:
        log.warning("Share: couldn't mark saved permissions due in %s: %s", _saved_path(rt), exc)


def _due_after(rt: Runtime) -> float:
    """Seconds until a walk is due again: share_rights_seconds, or with the share watcher up (which reads changed
    folders as they change) only now and then, as a safety net."""
    return WATCHED_WALK_SECONDS if rt.watching else rt.settings.share_rights_seconds


def _read_rights(rt: Runtime, key: tuple, walk, *, wait: bool = True) -> _Rights | None:
    """This person's permissions in a library: the last walk of its folders, and a new walk when that one is due.

    Only the first look (since the app started, or forget_rights) waits for a walk; requests arriving during it
    wait for that one instead of each doing their own. After that a request never waits: it gets the last walk,
    and a walk that's due (share_rights_seconds) runs in the background. A walk reads every folder's permissions
    from the disk, and on the box's library of 28,789 folders a request waited for one every 30 seconds ("Files
    loads, just really slowly"). Lists may lag the share by one walk; opening a file always asks the share now.
    After a restart, the walk saved before it (_load_rights) stands in for the first one, with a fresh walk behind:
    the share may have changed while the app was down.

    wait=False (a big library): with nothing known yet, start the first walk in the background and return None
    at once; the caller answers from what it reads itself meanwhile (library_reach)."""
    if not rt.owner:
        return _read_shared(rt, key, walk, wait=wait)
    cached = rt.rights.get(key)
    if cached is None and (saved := _load_rights(rt, key)) is not None:
        cached = rt.rights.setdefault(key, saved)  # another request may have loaded it a moment ago: use one copy
        if cached is saved:
            _walk_in_background(rt, key, walk)     # saved before a restart: check it against the share now
    if cached is not None:
        if time.monotonic() - cached.at >= _due_after(rt):
            _walk_in_background(rt, key, walk)     # due: refresh it, without making this request wait
        return cached
    if not wait:
        _walk_in_background(rt, key, walk)         # the first walk, behind the scenes
        return None
    with rt.rights_walks.setdefault(key, threading.Lock()):   # the very first look: everyone waits for one walk
        if (cached := rt.rights.get(key)) is not None:
            return cached                          # another request's walk just finished
        return _walk_rights(rt, key, walk)


# ------------------------------------------------- one process walks for all (Phase 5)
RELOAD_SECONDS = 30     # a process that doesn't walk reads share-rights.db again this often (sooner on "share:rights")
ASK_SECONDS = 20        # how long it waits for the share service's first walk of a key before walking it itself
POLL_SECONDS = 0.5      # how often it looks for that walk meanwhile


def _read_shared(rt: Runtime, key: tuple, walk, *, wait: bool) -> _Rights | None:
    """_read_rights for a process that doesn't walk: the share service's last walk of `key`, from share-rights.db
    (read again every RELOAD_SECONDS, or as soon as the service says the library changed). A key it hasn't walked
    yet: ask it ("share:walk") and wait up to ASK_SECONDS; no walk by then (the service down, or Valkey off so it
    never heard) and this process walks it itself, as before, so nobody waits for good. wait=False (a big library):
    None at once, and the caller answers provisionally (library_reach) until the walk is there."""
    now = time.monotonic()
    cached = rt.rights.get(key)
    if cached is not None and now - rt.loaded.get(key, 0.0) < RELOAD_SECONDS:
        return cached
    if (saved := _load_rights(rt, key)) is not None:
        rt.rights[key], rt.loaded[key] = saved, now
        rt.provisional.pop(key, None)
        return saved
    if cached is not None:
        return cached                              # the file couldn't be read just now: the last walk stands
    asked = rt.asked.setdefault(key, now)
    if asked == now:
        _ask_for_walk(key)
    if not wait:
        if now - asked >= ASK_SECONDS:             # nobody answered: this process walks it, behind the scenes
            _walk_in_background(rt, key, walk)
        return None
    with rt.rights_walks.setdefault(key, threading.Lock()):
        while time.monotonic() - asked < ASK_SECONDS:
            if (saved := _load_rights(rt, key)) is not None:
                rt.rights[key], rt.loaded[key] = saved, time.monotonic()
                return saved
            time.sleep(POLL_SECONDS)
        if (cached := rt.rights.get(key)) is not None:
            return cached                          # another request's own walk finished meanwhile
        log.info("Share: no walk from the share service for library %s within %ds; walking it here.",
                 key[0], ASK_SECONDS)
        return _walk_rights(rt, key, walk)


def _ask_for_walk(key: tuple) -> None:
    library, uid, gids = key
    try:
        events.publish_sync("share:walk", {"library": int(library), "uid": int(uid), "gids": sorted(int(g) for g in gids)})
    except Exception:                              # Valkey gone: this process walks it after ASK_SECONDS
        log.warning("Share: couldn't ask the share service for a walk.", exc_info=True)


def _announce(library_id: int | None) -> None:
    """Tell the other processes that a library's permissions changed (None: all of them)."""
    try:
        events.publish_sync("share:rights", {"library": library_id})
    except Exception:
        log.warning("Share: couldn't tell the other processes about changed permissions.", exc_info=True)


def heard(library_id: int | None) -> None:
    """The share service changed a library's permissions (None: every library): read them again at the next look."""
    rt = _runtime
    if rt is None:
        return
    for key in list(rt.loaded):
        if library_id is None or key[0] == library_id:
            rt.loaded.pop(key, None)
    if library_id is None:
        rt.rights.clear()                          # forgotten everywhere: the next look reads the file again


async def follow_changes() -> None:
    """For a process that doesn't walk: hear "share:rights" for as long as it runs (a background task)."""
    import asyncio

    while True:
        try:
            async for _, data in events.subscribe("share:rights"):
                heard(data.get("library"))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Share: lost the event bus; reading share-rights.db every %ds meanwhile.", RELOAD_SECONDS,
                        exc_info=True)
            await asyncio.sleep(5)


def walk_for(rt: Runtime, conn: sqlite3.Connection, key: tuple):
    """The walk of `key` (library, uid, gids) built from the database alone: what library_reach builds for a request,
    for the share service, which walks for people it has never seen a request from. None: no such share library."""
    library, uid, gids = key
    row = conn.execute("SELECT * FROM collections WHERE id = ? AND kind = 'library' AND share_path IS NOT NULL",
                       (library,)).fetchone()
    if row is None:
        return None
    info, check = _checker(rt, conn, row, uid, gids)
    return lambda: {fid: check(fid) for fid in (None, *info)}


def serve_walks_once(rt: Runtime, key: tuple) -> bool:
    """The share service: walk `key` now (unless a walk of it is under way), save it and say so. True: walked."""
    lock = rt.rights_walks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        return False
    try:
        conn = db.connect(rt.settings.db_path, features=True)
        try:
            walk = walk_for(rt, conn, key)
            if walk is None:
                return False
            _walk_rights(rt, key, walk)
        finally:
            conn.close()
    finally:
        lock.release()
    _announce(key[0])
    return True


def due_keys(rt: Runtime) -> list[tuple]:
    """The share service: every saved walk that's due again (_due_after), oldest first."""
    if not _saved_path(rt).exists():
        return []
    try:
        conn = _saved_db(rt)
        try:
            rows = conn.execute("SELECT library, uid, gids FROM rights WHERE walked_at < ? ORDER BY walked_at",
                                (time.time() - _due_after(rt),)).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        log.warning("Share: couldn't list due walks in %s: %s", _saved_path(rt), exc)
        return []
    return [(lib, uid, frozenset(int(g) for g in gids.split(",") if g)) for lib, uid, gids in rows]


async def serve_walks() -> None:
    """The share service, for as long as it runs: walks asked for ("share:walk") as they come, and every minute
    the saved walks that are due (the watcher keeps them current in between)."""
    import asyncio

    rt = _runtime
    if rt is None:
        return

    async def asked() -> None:
        while True:
            try:
                async for _, data in events.subscribe("share:walk"):
                    key = (int(data["library"]), int(data["uid"]), frozenset(int(g) for g in data.get("gids") or ()))
                    asyncio.get_running_loop().run_in_executor(None, serve_walks_once, rt, key)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Share: lost the event bus for walk requests; trying again.", exc_info=True)
                await asyncio.sleep(5)

    async def due() -> None:
        while True:
            await asyncio.sleep(60)
            for key in await asyncio.to_thread(due_keys, rt):
                try:
                    await asyncio.to_thread(serve_walks_once, rt, key)
                except Exception:
                    log.exception("Share: a due walk failed")

    await asyncio.gather(asked(), due())


def _walk_rights(rt: Runtime, key: tuple, walk) -> _Rights:
    """One walk of a library's folders on the share, kept under `key`. Call holding the key's walk lock."""
    began = time.monotonic()
    rights = _Rights(began, walk())
    rt.rights[key] = rights
    rt.provisional.pop(key, None)                  # a first look's partial answer: the whole walk replaces it
    _save_rights(rt, key, rights)                  # for the first look after a restart
    for old in [k for k, r in list(rt.rights.items()) if began - r.at > 600]:   # not looked at for 10 min: free it
        rt.rights.pop(old, None)
    return rights


def _walk_in_background(rt: Runtime, key: tuple, walk) -> None:
    """Start a walk for `key` on a thread of its own, unless one is running already. The walk only reads the share
    and the folder rows it was given, never the request's database connection."""
    lock = rt.rights_walks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        return                                     # a walk for this key is already under way

    def run() -> None:
        try:
            _walk_rights(rt, key, walk)
        except Exception:                          # the share away, a folder gone mid-walk: keep the last walk
            log.exception("Share: refreshing permissions in the background failed")
        finally:
            lock.release()

    threading.Thread(target=run, name="share-rights-walk", daemon=True).start()


def _checker(rt: Runtime, conn: sqlite3.Connection, row: sqlite3.Row, uid: int, gids):
    """The library's folders (id -> row), and check(folder id or None) -> (read, write): what the share says now
    about one folder for uid/gids. library_reach's and the share service's (walk_for)."""
    base = rt.root / Path(row["share_path"])
    info = db.folder_rows(conn, row["id"])        # from memory unless the library's folders changed
    paths: dict[int, Path | None] = {}

    def path_of(fid: int) -> Path | None:
        # A binned folder sits in the trash with its own permissions (a rename
        # keeps them), and what's inside it below that.
        if fid not in paths:
            f = info[fid]
            if f["trash_path"]:
                paths[fid] = rt.trash / f["trash_path"]
            else:
                parent = base if f["parent_id"] is None else path_of(f["parent_id"])
                paths[fid] = None if parent is None else parent / f["name"]
        return paths[fid]

    def check(fid: int | None) -> tuple[bool, bool]:
        """What the share says now about this one folder (None: the library's top)."""
        path = base if fid is None else path_of(fid) if fid in info else None
        return rt.checker.rights(path, uid, gids) if path is not None else (False, False)

    return info, check


def library_reach(conn: sqlite3.Connection, row: sqlite3.Row, user: sqlite3.Row):
    """What `user` may do in a share library, from the share's permissions
    (permissions.Reach), or None for nothing. Folder rows always come fresh from the database; what the
    share allows comes from _read_rights, and only a folder it hasn't seen yet costs a look at the disk."""
    from mnemerynne.permissions import Reach

    rt = _runtime
    if rt is None or not user["ad_account"]:
        return None
    ids = rt.directory.ids_of(user["ad_account"])
    if ids is None:
        return None
    uid, gids = ids
    info, check = _checker(rt, conn, row, uid, gids)
    folders = list(info.values())

    key = (row["id"], uid, gids)
    walk = lambda: {fid: check(fid) for fid in (None, *info)}   # noqa: E731  every folder, from the disk
    big = len(info) > LAZY_FROM                    # a whole walk would keep this request waiting
    rights = _read_rights(rt, key, walk, wait=not big)
    provisional = rights is None
    if provisional:
        # A big library nobody here has walked yet (a first visit, new groups): answer now like a file browser
        # does, from the few folders read so far, while the whole walk runs behind. Folders not read yet count
        # as closed, so this can only ever show less than the share allows, never more. The library's top and
        # its top-level folders are read now; a page opened reads its own folder and what it shows
        # (fs.Access.settle), and what's read is kept for the next request until the walk replaces it.
        rights = rt.provisional.get(key)
        if rights is None:
            tops = [f["id"] for f in folders if f["parent_id"] is None]
            rights = rt.provisional.setdefault(key, _Rights(time.monotonic(),
                                                            {fid: check(fid) for fid in (None, *tops)}))

    def live(fid: int | None) -> tuple[bool, bool]:
        """Reach.recheck: reads the share now, and corrects the cache so the next list agrees."""
        rights.folders[fid] = found = check(fid)
        return found

    root_read, root_write = rights.folders[None]
    claude_on = bool(row["claude_enabled"])
    reach = Reach(row["id"], "library", row["name"], root_read, root_write, claude_on and root_read,
                  parents={f["id"]: f["parent_id"] for f in folders}, live=live)
    for fid in info:
        found = rights.folders.get(fid)
        if found is None and provisional:
            reach.unknown.add(fid)                 # not read yet: closed for now (Access.settle reads it on demand)
            continue
        read, write = found if found is not None else live(fid)   # a folder made since the last walk
        if read:
            reach.read.add(fid)
            if claude_on:
                reach.claude.add(fid)
        if write:
            reach.write.add(fid)
    if not (reach.root_read or reach.read):
        return None   # nothing here for them; on a first look, nothing found yet (never shown on a guess)
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
    if path.is_dir() and any(path.iterdir()):
        # Someone else's files (a deleted user's, or planted): never hand them
        # to this inbox. Keep them in the trash for an admin.
        keep = rt.trash / trash_name("inbox", collection_id, f"{username}-{int(time.time())}")
        log.warning("Share: %s already had files; moved them to %s.", path, keep)
        rename(path, keep)
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
                _clear_stale_stage(entry)
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


def name_problem(name: str) -> str | None:
    """Why the file manager can't keep this name exactly as it is on disk
    (shown in the scan's skipped list, so people know what to rename); None if it can."""
    from mnemerynne.ingest import store

    try:
        check_name(name)
    except ValueError as exc:
        return str(exc)
    if store.clean_display_name(name) == name:
        return None
    if len(name) > 200:
        return "the name is longer than 200 characters"
    if name != name.strip():
        return "the name starts or ends with a space"
    if any(unicodedata.category(ch)[0] == "C" for ch in name):
        return "the name has invisible control characters"
    if re.search(r"\s{2,}|[^\S ]", name):
        return "the name has two spaces in a row (or a tab)"
    return "the name can't be kept exactly as it is"


def _usable(name: str) -> bool:
    """A name the file manager can keep exactly as it is on disk."""
    return name_problem(name) is None


def _discover(conn: sqlite3.Connection, rt: Runtime, stats: Stats) -> None:
    """Top-level folders become libraries; an empty Users/<name> attaches to
    that user's empty inbox."""
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
                    "SELECT c.id, u.ad_account FROM collections c JOIN users u ON u.id = c.owner_user_id "
                    "WHERE c.kind = 'private' AND c.share_path IS NULL AND u.username = ? COLLATE NOCASE "
                    "AND NOT EXISTS (SELECT 1 FROM files WHERE collection_id = c.id)", (sub.name,)).fetchone()
                if inbox is None:
                    continue
                if any(os.scandir(sub.path)):
                    # Files someone put there are not the owner's to inherit.
                    stats.skipped.append(f"{share_path}: not empty, so not taken as {sub.name}'s inbox")
                    continue
                conn.execute("UPDATE collections SET share_path = ?, share_inode = ? WHERE id = ?",
                             (share_path, sub.inode(), inbox["id"]))
                secure_inbox(Path(sub.path), inbox["ad_account"])
            continue
        if entry.name.lower() in known:
            continue
        renamed = by_inode.get(entry.inode())
        if renamed is not None and renamed["kind"] == "library" and not (rt.root / renamed["share_path"]).exists():
            conn.execute("UPDATE collections SET share_path = ?, name = ? WHERE id = ?",
                         (entry.name, entry.name, renamed["id"]))
            log.info("Share: library %s was renamed to %s on the share.", renamed["name"], entry.name)
            continue
        if (problem := name_problem(entry.name)) is not None:
            stats.skipped.append(f"{entry.name}: can't be a library: {problem}; rename it")
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


@dataclass
class _Read:
    """What pass 1 learned about a file's content, for pass 2 to apply."""
    sha: str
    size: int
    mtime_ns: int
    inspected: tuple | None   # (kind, mime, size, note) when the blob is new


def _same_place(conn: sqlite3.Connection, rt: Runtime, collection_id: int, base: Path) -> bool:
    """Under the lock: the collection still lives at `base`, and the share is mounted."""
    row = conn.execute("SELECT share_path FROM collections WHERE id = ?", (collection_id,)).fetchone()
    return (row is not None and row["share_path"] is not None and rt.root / row["share_path"] == base
            and mounted(rt) and base.is_dir())


@dataclass
class _Plan:
    """One collection in a scan: pass 1's view of it, and the little that needs changing."""
    cid: int
    base: Path
    disk_folders: dict[tuple[str, ...], int]
    disk_files: dict[tuple[str, ...], _DiskFile]
    db_folders: dict[tuple[str, ...], int]
    db_files: dict[tuple[str, ...], sqlite3.Row]
    new_folders: list[tuple[str, ...]] = field(default_factory=list)   # on disk only, parents first
    to_apply: list[tuple[str, ...]] = field(default_factory=list)      # files pass 2 has to look at


def _new_folders(plan: _Plan, stats: Stats) -> list[tuple[str, ...]]:
    """Folders on disk the database doesn't have, parents first; ones whose name can't be kept (and anything
    inside them) are reported here and never reach the write lock."""
    out: list[tuple[str, ...]] = []
    kept: set[tuple[str, ...]] = set()
    for path in sorted((p for p in plan.disk_folders if p not in plan.db_folders), key=len):
        if len(path) > 1 and path[:-1] not in plan.db_folders and path[:-1] not in kept:
            continue   # inside a folder that was skipped
        if (problem := name_problem(path[-1])) is not None:
            stats.skipped.append(f"{'/'.join(path)}: {problem}; rename it")
            continue
        kept.add(path)
        out.append(path)
    return out


def sync(conn: sqlite3.Connection, settings: Settings | None = None) -> Stats:
    """Bring the database in line with the share. Safe to run while people use
    the file manager: reading, comparing and hashing happen without a lock, the
    write lock is taken only for a collection where something differs, and every
    change is checked again under it before it is applied."""
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
    now_ns = time.time_ns()
    settle_before = now_ns - settings.share_settle_seconds * 1_000_000_000
    # A write stamps the server's own clock; a time well ahead of it was set by a copy tool (robocopy, an unzip)
    # from a machine whose clock is off, and the file is finished: waiting for it would hide it for hours.
    copied_stamp = now_ns + 60 * 1_000_000_000

    # Pass 1, no lock: what is on disk and in the database, and which few things differ.
    plans = []
    for c in conn.execute("SELECT id, share_path FROM collections WHERE share_path IS NOT NULL").fetchall():
        base = rt.root / c["share_path"]
        if not base.is_dir():
            continue
        disk_folders, disk_files = _walk(base)
        db_folders, db_files = _db_tree(conn, c["id"])
        plans.append(_Plan(c["id"], base, disk_folders, disk_files, db_folders, db_files))

    gone: dict[int, tuple[int, tuple[str, ...], sqlite3.Row]] = {}   # inode -> (collection, path, row)
    for plan in plans:
        for path, row in plan.db_files.items():
            if path not in plan.disk_files and row["disk_inode"]:
                gone[row["disk_inode"]] = (plan.cid, path, row)

    # Still no lock: hash new and changed files, and inspect new content.
    reads: dict[tuple[int, tuple[str, ...]], _Read] = {}
    for plan in plans:
        cid, base = plan.cid, plan.base
        plan.new_folders = _new_folders(plan, stats)
        making = set(plan.new_folders)                               # folders pass 2 is going to add
        for path, f in plan.disk_files.items():
            row = plan.db_files.get(path)
            if row is not None and (row["disk_size"], row["disk_mtime_ns"]) == (f.size, f.mtime_ns):
                continue   # the usual case on a big share: known and unchanged
            maybe_moved = row is None and f.inode in gone
            if maybe_moved:
                plan.to_apply.append(path)                           # pass 2 decides whether it really moved
            if settle_before < f.mtime_ns <= copied_stamp:
                continue   # still being written: the next scan reads it
            if (problem := name_problem(path[-1])) is not None:
                stats.skipped.append(f"{'/'.join(path)}: {problem}; rename it")
                continue
            old = gone.get(f.inode)
            if row is None and old and (old[2]["disk_size"], old[2]["disk_mtime_ns"]) == (f.size, f.mtime_ns):
                continue   # a rename or move, content untouched: no need to read it
            if len(path) > 1 and path[:-1] not in plan.db_folders and path[:-1] not in making:
                continue   # its folder was skipped: reading it again every scan would change nothing
            full = base.joinpath(*path)
            try:
                sha = hash_file(full)
                known = conn.execute("SELECT 1 FROM blobs WHERE sha256 = ?", (sha,)).fetchone()
                inspected = None if known else store.inspect(full, path[-1], settings)
            except OSError as exc:
                stats.skipped.append(f"{'/'.join(path)}: {exc}")
                continue
            reads[(cid, path)] = _Read(sha, f.size, f.mtime_ns, inspected)
            if not maybe_moved:
                plan.to_apply.append(path)                           # new or changed content for pass 2

    def unchanged(full: Path, size: int, mtime_ns: int) -> bool:
        try:
            return disk_stat(full)[:2] == (size, mtime_ns)
        except OSError:
            return False

    # Pass 2, per collection under the write lock, only where pass 1 found something: re-check, then add, move, update.
    for plan in plans:
        if not (plan.new_folders or plan.to_apply):
            continue   # nothing to do here: everyone else keeps the database to themselves
        cid, base = plan.cid, plan.base
        with transaction(conn):
            if not _same_place(conn, rt, cid, base):
                continue
            db_folders, db_files = _db_tree(conn, cid)
            folder_ids = dict(db_folders)
            for path in plan.new_folders:
                if path in folder_ids or not base.joinpath(*path).is_dir():
                    continue
                parent = folder_ids.get(path[:-1]) if len(path) > 1 else None
                if len(path) > 1 and parent is None:
                    continue   # its parent was skipped
                if fs._taken(conn, cid, parent, path[-1], "folder"):
                    stats.skipped.append(f"{'/'.join(path)}: another folder differs only in upper/lower case")
                    continue
                folder_ids[path] = fs.ensure_path(conn, cid, parent, [path[-1]], None)
                stats.folders_added += 1

            for path in plan.to_apply:
                f = plan.disk_files[path]
                full = base.joinpath(*path)
                folder_id = folder_ids.get(path[:-1]) if len(path) > 1 else None
                if len(path) > 1 and folder_id is None:
                    continue
                read = reads.get((cid, path))
                row = db_files.get(path)
                if row is not None:
                    # Changed in place (re-checked: not edited again since it was hashed).
                    if read is None or not unchanged(full, read.size, read.mtime_ns):
                        continue
                    if read.sha != row["blob_sha256"]:
                        note = _new_blob(conn, read)
                        status = "stored" if _blob_kind(conn, read.sha) == "other" else "queued"
                        conn.execute("UPDATE files SET blob_sha256 = ?, status = ?, error = NULL, progress = NULL, "
                                     "notes = ?, updated_at = datetime('now') WHERE id = ?",
                                     (read.sha, status, note, row["id"]))
                        if status == "queued":
                            jobs.enqueue(conn, "ingest", row["id"])
                        _drop_unused_blob(conn, settings, row["blob_sha256"])
                        stats.changed += 1
                    record_stat(conn, row["id"], full)
                    continue
                try:
                    size, mtime_ns, inode = disk_stat(full)
                except OSError:
                    continue
                if inode != f.inode:
                    continue
                moved = gone.get(inode)
                if moved is not None:
                    old_cid, old_path, old_row = moved
                    current = conn.execute("SELECT * FROM files WHERE id = ? AND deleted_at IS NULL",
                                           (old_row["id"],)).fetchone()
                    old_share = conn.execute("SELECT share_path FROM collections WHERE id = ?",
                                             (old_cid,)).fetchone()
                    # Only a move if the content is untouched (same size and
                    # mtime as when it was last read); otherwise it's new content.
                    if (current is not None and old_share is not None and old_share["share_path"]
                            and not (rt.root / old_share["share_path"]).joinpath(*old_path).exists()
                            and (current["disk_size"], current["disk_mtime_ns"]) == (size, mtime_ns)
                            and not fs._taken(conn, cid, folder_id, path[-1], "file")):
                        conn.execute("UPDATE files SET collection_id = ?, folder_id = ?, display_name = ?, "
                                     "updated_at = datetime('now') WHERE id = ?",
                                     (cid, folder_id, path[-1], old_row["id"]))
                        record_stat(conn, old_row["id"], full)
                        fs._sync_scope(conn, [old_row["id"]])
                        del gone[inode]
                        stats.moved += 1
                        continue
                if read is None or not unchanged(full, read.size, read.mtime_ns):
                    continue
                if fs._taken(conn, cid, folder_id, path[-1], "file"):
                    stats.skipped.append(f"{'/'.join(path)}: another file differs only in upper/lower case")
                    continue
                note = _new_blob(conn, read)
                kind = _blob_kind(conn, read.sha)
                file_id = conn.execute(
                    "INSERT INTO files(collection_id, folder_id, blob_sha256, display_name, status, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (cid, folder_id, read.sha, path[-1], "stored" if kind == "other" else "queued", note)).lastrowid
                record_stat(conn, file_id, full)
                if kind != "other":
                    jobs.enqueue(conn, "ingest", file_id)
                stats.added += 1

    # Pass 3, after every collection's additions (so moves between collections
    # were recognised first): what is gone from disk, found without the lock and re-checked under it.
    for plan in plans:
        cid, base = plan.cid, plan.base
        lost_files = [p for p in plan.db_files if p not in plan.disk_files and not base.joinpath(*p).exists()]
        lost_folders = [p for p in plan.db_folders if p not in plan.disk_folders and not base.joinpath(*p).is_dir()]
        if not (lost_files or lost_folders):
            continue   # the usual case: nothing vanished from this collection
        with transaction(conn):
            if not _same_place(conn, rt, cid, base):
                continue
            db_folders, db_files = _db_tree(conn, cid)
            for path in lost_files:
                row = db_files.get(path)                             # None: moved in pass 2, or already removed
                if row is not None and not base.joinpath(*path).exists():
                    store.delete_record(conn, settings, row["id"])
                    stats.removed += 1
            for path in sorted(lost_folders, key=lambda p: -len(p)):   # deepest first, so parents empty out
                fid = db_folders.get(path)
                if fid is not None and not base.joinpath(*path).is_dir():
                    if conn.execute("SELECT 1 FROM files WHERE folder_id = ? AND deleted_at IS NULL", (fid,)).fetchone():
                        continue
                    conn.execute("UPDATE files SET folder_id = NULL WHERE folder_id = ?", (fid,))  # binned ones
                    conn.execute("UPDATE folders SET parent_id = NULL WHERE parent_id = ? AND deleted_at IS NOT NULL",
                                 (fid,))
                    conn.execute("DELETE FROM folders WHERE id = ?", (fid,))
                    stats.folders_removed += 1

    # Libraries whose folder is gone from the (still mounted) share; looked for before taking the lock.
    shares = conn.execute("SELECT share_path FROM collections WHERE kind = 'library' AND share_path IS NOT NULL").fetchall()
    if any(not (rt.root / c["share_path"]).is_dir() for c in shares):
        with transaction(conn):
            if mounted(rt):
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


def _new_blob(conn: sqlite3.Connection, read: _Read) -> str | None:
    """A blob row for content read from the share (the bytes stay there).
    Returns why it won't be searchable, if the blob is new and that applies."""
    from mnemerynne.ingest import store

    if conn.execute("SELECT 1 FROM blobs WHERE sha256 = ?", (read.sha,)).fetchone() or read.inspected is None:
        return None
    kind, mime, size, note = read.inspected
    conn.execute("INSERT INTO blobs(sha256, kind, mime, size, rel_path) VALUES (?, ?, ?, ?, ?)",
                 (read.sha, kind, mime, size, store.blob_rel_path(read.sha, kind)))
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


# -------------------------------------------------------------------- export
@dataclass
class Export:
    """What exporting one collection to the share did (or, dry run, would do)."""
    collection: str
    share_path: str
    folders: int = 0
    files: int = 0
    bytes: int = 0
    renamed: list[str] = field(default_factory=list)
    problem: str | None = None
    done: bool = False


def safe_name(name: str) -> str:
    """A name Windows accepts: bad characters become "_", no trailing dot or
    space, reserved device names get a leading "_"."""
    cleaned = "".join("_" if ch in _BAD_CHARS else ch for ch in name).rstrip(". ") or "_"
    if cleaned.split(".")[0].strip().lower() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def _target(conn: sqlite3.Connection, rt: Runtime, row: sqlite3.Row) -> str:
    if row["kind"] == "library":
        return row["name"]
    owner = conn.execute("SELECT username FROM users WHERE id = ?", (row["owner_user_id"],)).fetchone()
    return f"{rt.settings.users_folder}/{owner['username']}"


def _fix_names(conn: sqlite3.Connection, collection_id: int, apply: bool) -> list[str]:
    """Rename folders and files whose names Windows can't use."""
    from mnemerynne import fs
    from mnemerynne.db import transaction

    renamed = []

    def run() -> None:
        for kind, table, column, parent in (("folder", "folders", "name", "parent_id"),
                                             ("file", "files", "display_name", "folder_id")):
            for r in conn.execute(f"SELECT id, {column} AS name, {parent} AS parent FROM {table} "
                                  f"WHERE collection_id = ? ORDER BY id", (collection_id,)).fetchall():
                if safe_name(r["name"]) == r["name"]:
                    continue
                new = fs.unique_name(conn, collection_id, r["parent"], safe_name(r["name"]), kind, r["id"])
                renamed.append(f"{kind} “{r['name']}” → “{new}”")
                if apply:
                    conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (new, r["id"]))

    if apply:
        with transaction(conn):
            run()
    else:
        run()
    return renamed


def _snapshot(conn: sqlite3.Connection, collection_id: int):
    folders = conn.execute("SELECT id, parent_id, name, deleted_at FROM folders WHERE collection_id = ? ORDER BY id",
                           (collection_id,)).fetchall()
    files = conn.execute("SELECT f.id, f.folder_id, f.display_name, f.blob_sha256, f.deleted_at, b.rel_path, b.size "
                         "FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.collection_id = ? ORDER BY f.id",
                         (collection_id,)).fetchall()
    return [tuple(r) for r in folders], [tuple(r) for r in files], folders, files


def export(conn: sqlite3.Connection, collection_id: int, *, dry_run: bool = False) -> Export:
    """Move a collection stored inside Mnemerynne onto the share, keeping every
    file's id and search index. Files are copied without holding the database
    lock; the switch is one short transaction that first checks nobody changed
    the collection meanwhile (if they did, the copy is undone: run it again)."""
    from mnemerynne.db import transaction
    from mnemerynne.ingest import store

    rt = _rt()
    row = collection_row(conn, collection_id)
    target = _target(conn, rt, row)
    result = Export(row["name"], target)
    if row["share_path"]:
        result.problem = "already on the share"
        return result
    try:
        check_name(target.split("/")[-1])
    except ValueError as exc:
        result.problem = f"rename the library first: {exc}"
        return result
    if row["kind"] == "library" and target.lower() == rt.settings.users_folder.lower():
        result.problem = f"rename the library first: “{target}” is kept for inboxes"
        return result
    base = rt.root / target
    if base.exists() and (not base.is_dir() or any(base.iterdir())):
        result.problem = f"{base} already exists and isn't empty; rename one of them first"
        return result
    if not rt.trash.is_dir():
        result.problem = f"{rt.trash} doesn't exist; run share-init first"
        return result

    result.renamed = _fix_names(conn, collection_id, apply=not dry_run)
    folder_snap, file_snap, folders, files = _snapshot(conn, collection_id)
    by_id = {f["id"]: f for f in folders}
    paths: dict[int, Path] = {}

    def folder_path(fid: int) -> Path:
        # Same layout share.folder_dir() will compute: binned folders in the trash.
        if fid not in paths:
            f = by_id[fid]
            if f["deleted_at"]:
                paths[fid] = rt.trash / trash_name("d", fid, f["name"])
            else:
                paths[fid] = (base if f["parent_id"] is None else folder_path(f["parent_id"])) / f["name"]
        return paths[fid]

    def file_dest(f) -> Path:
        if f["deleted_at"]:
            return rt.trash / trash_name("f", f["id"], f["display_name"])
        return (base if f["folder_id"] is None else folder_path(f["folder_id"])) / f["display_name"]

    result.folders, result.files = len(folders), len(files)
    result.bytes = sum(f["size"] for f in files)
    missing = [f["display_name"] for f in files if not store.blob_path(rt.settings, f["rel_path"]).is_file()]
    if missing:
        result.problem = f"{len(missing)} file(s) are missing from blob storage, e.g. {missing[0]}"
        return result
    if dry_run:
        return result

    created: list[Path] = []   # undone if anything goes wrong
    try:
        if not base.exists():
            make_dir(base)
            created.append(base)
        if row["kind"] == "private":
            owner = conn.execute("SELECT ad_account FROM users WHERE id = ?", (row["owner_user_id"],)).fetchone()
            secure_inbox(base, owner["ad_account"])   # before the files, so they inherit it
        for fid in sorted(by_id, key=lambda i: len(folder_path(i).parts)):
            path = folder_path(fid)
            if path.exists():
                raise ShareError(f"{path} already exists")
            make_dir(path)
            created.append(path)
        for f in files:
            dest = file_dest(f)
            if dest.exists():
                raise ShareError(f"{dest} already exists")
            created.append(dest)   # ours from here on, even half-written
            copy_file(store.blob_path(rt.settings, f["rel_path"]), dest)
            if dest.stat().st_size != f["size"]:
                raise ShareError(f"the copy of {f['display_name']} came out the wrong size")

        with transaction(conn):
            now_folders, now_files, _, _ = _snapshot(conn, collection_id)
            current = collection_row(conn, collection_id)
            if now_folders != folder_snap or now_files != file_snap or current["share_path"] or not mounted(rt):
                raise ShareError("the collection changed while it was being copied; run the export again")
            conn.execute("UPDATE collections SET share_path = ?, share_inode = ? WHERE id = ?",
                         (target, base.stat().st_ino, collection_id))
            for fid, f in by_id.items():
                if f["deleted_at"]:
                    conn.execute("UPDATE folders SET trash_path = ? WHERE id = ?",
                                 (trash_name("d", fid, f["name"]), fid))
            for f in files:
                if f["deleted_at"]:
                    conn.execute("UPDATE files SET trash_path = ? WHERE id = ?",
                                 (trash_name("f", f["id"], f["display_name"]), f["id"]))
                record_stat(conn, f["id"], file_dest(f))
    except BaseException:
        for path in reversed(created):
            try:
                remove(path)
            except ShareError:
                log.warning("Share: couldn't undo %s after a failed export", path)
        raise

    # Blob copies nothing outside the share uses any more (OCR'd copies stay:
    # they are what the PDF viewer shows).
    for f in files:
        still = conn.execute("SELECT 1 FROM files x JOIN collections c ON c.id = x.collection_id "
                             "WHERE x.blob_sha256 = ? AND c.share_path IS NULL LIMIT 1", (f["blob_sha256"],)).fetchone()
        if not still:
            store.blob_path(rt.settings, f["rel_path"]).unlink(missing_ok=True)
    result.done = True
    log.info("Share: exported %s to %s (%d files).", row["name"], base, len(files))
    return result
