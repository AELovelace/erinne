"""Claude changing files: the Agents folder, who may edit what, saving with
version checks, earlier versions and restoring them.

Claude may change a file only where all of these hold:
  * the token allows editing (api_tokens.can_edit),
  * the person can edit that place themselves,
  * the place is shared with Claude, and
  * the collection allows Claude to edit (collections.claude_edit, off by
    default), or the file is in the person's own Agents folder, which every
    inbox has and where editing is on by default.

Every change keeps the previous content under <data_dir>/versions first, and
must name the version it was made from: if the file changed since (someone
saved it in Word over SMB, or another edit landed), the change is refused.
On the share, a file open in Word or Excel (a ~$ lock file next to it) is
refused too, and the new content replaces the old in one rename, so nobody
ever sees half a file. The file manager's code paths (fs, store) are reused
for creating files; the format work is in editing_formats.py.
"""

from __future__ import annotations

import hashlib
import io
import os
import secrets
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mnemerynne import fs, jobs, permissions, share
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.ingest import store

AGENTS = "Agents"
# extension -> format handled by editing_formats
EDITABLE = {".txt": "text", ".text": "text", ".md": "text", ".markdown": "text",
            ".docx": "docx", ".xlsx": "xlsx"}


class EditError(ValueError):
    """Refused; the message is safe to show (and tells Claude what to do)."""
    status = 400


class EditNotFound(EditError):
    status = 404


class EditForbidden(EditError):
    status = 403


class EditConflict(EditError):
    status = 409


def format_of(name: str) -> str | None:
    return EDITABLE.get(PurePosixPath(name.lower()).suffix)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------ Agents folder
def agents_folder(conn: sqlite3.Connection, user_id: int, *, create: bool = True) -> int | None:
    """The person's Agents folder (top level of their inbox), made if missing.
    An existing top-level folder called Agents is adopted. None when there is
    no inbox, or (create=False) no folder yet."""
    cid = permissions.private_collection_id(conn, user_id)
    if cid is None:
        return None
    row = conn.execute("SELECT id FROM folders WHERE collection_id = ? AND role = 'agents' AND deleted_at IS NULL",
                       (cid,)).fetchone()
    if row and fs.is_live_folder(conn, row["id"]):
        return row["id"]
    if not create:
        return None
    with transaction(conn):
        existing = conn.execute(
            "SELECT id FROM folders WHERE collection_id = ? AND parent_id IS NULL AND lower(name) = lower(?) "
            "AND deleted_at IS NULL", (cid, AGENTS)).fetchone()
        folder_id = existing["id"] if existing else fs.ensure_path(conn, cid, None, [AGENTS], user_id)
        if existing and share.is_share(conn, cid):
            fs._disk(lambda: share.make_dir(share.folder_dir(conn, cid, folder_id)))
        conn.execute("UPDATE folders SET role = 'agents' WHERE id = ?", (folder_id,))
    return folder_id


def ensure_all_agents(conn: sqlite3.Connection) -> int:
    """Give every inbox its Agents folder (start-up). Returns how many were made;
    an inbox whose share is unavailable just gets it later."""
    made = 0
    for row in conn.execute("SELECT owner_user_id FROM collections WHERE kind = 'private'").fetchall():
        try:
            if agents_folder(conn, row["owner_user_id"], create=False) is None:
                agents_folder(conn, row["owner_user_id"])
                made += 1
        except (fs.FsError, OSError):
            continue
    return made


def _agents_subtree(conn: sqlite3.Connection, collection_id: int) -> set[int]:
    rows = conn.execute("SELECT id, parent_id, role FROM folders WHERE collection_id = ? AND deleted_at IS NULL",
                        (collection_id,)).fetchall()
    tops = {r["id"] for r in rows if r["role"] == "agents"}
    parents = {r["id"]: r["parent_id"] for r in rows}
    return {f for f in parents if permissions._under(f, tops, parents)}


# ---------------------------------------------------------------- access
@dataclass
class Place:
    """Where Claude may edit for a caller, and why."""
    collection_id: int
    folder_id: int | None
    reason: str          # 'agents' | 'library'


def check_edit(conn: sqlite3.Connection, user_id: int, token_can_edit: bool,
               collection_id: int, folder_id: int | None) -> Place:
    """Raise unless Claude may change files at this place for this person."""
    claude = fs.Access.for_claude(conn, user_id)
    if not claude.can_read(collection_id, folder_id):
        raise EditNotFound("No such place (or it isn't shared with Claude).")
    if not token_can_edit:
        raise EditForbidden("This connection can only search. To let Claude change files, open Claude in "
                            "Mnemerynne and turn on “Let Claude edit files” for this token.")
    if not fs.Access.of(conn, user_id).can_write(collection_id, folder_id):
        raise EditForbidden("You can't change files here yourself, so Claude can't either.")
    coll = conn.execute("SELECT kind, owner_user_id, claude_edit FROM collections WHERE id = ?",
                        (collection_id,)).fetchone()
    if coll["claude_edit"]:
        return Place(collection_id, folder_id, "library")
    if coll["kind"] == "private" and coll["owner_user_id"] == user_id and folder_id in _agents_subtree(conn, collection_id):
        return Place(collection_id, folder_id, "agents")
    where = "your inbox outside the Agents folder" if coll["kind"] == "private" else "this library"
    raise EditForbidden(f"Claude isn't allowed to change files in {where}. Work in your {AGENTS} folder "
                        f"instead (create_file puts new files there), or ask an admin to let Claude edit here.")


def editable_places(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Where Claude may edit for this person (ignoring the token), for display."""
    out = []
    try:
        agents = agents_folder(conn, user_id)
    except (fs.FsError, OSError):
        agents = None   # the share is away; it's made next time
    if agents is not None:
        out.append({"library": "My inbox", "folder": AGENTS, "why": "your Agents folder"})
    access = fs.Access.of(conn, user_id)
    claude = fs.Access.for_claude(conn, user_id)
    for row in conn.execute("SELECT id, kind, name FROM collections WHERE claude_edit = 1").fetchall():
        reach = access.reaches.get(row["id"])
        if reach is None or not reach.any_write or row["id"] not in claude.readable:
            continue
        out.append({"library": "My inbox" if row["kind"] == "private" else row["name"],
                    "folder": None if reach.root_write else "some folders", "why": "allowed by an admin"})
    return out


# ------------------------------------------------------------------ reading
@dataclass
class Loaded:
    row: sqlite3.Row
    path: Path
    data: bytes
    version: str
    fmt: str


def load(conn: sqlite3.Connection, settings: Settings, user_id: int, file_id: int) -> Loaded:
    """A file Claude may read, with its current bytes and version."""
    row = fs.file_row(conn, file_id)
    if row is None or row["deleted_at"] is not None or not fs.is_live_folder(conn, row["folder_id"]) \
            or not fs.Access.for_claude(conn, user_id).can_read(row["collection_id"], row["folder_id"]):
        raise EditNotFound("No such file (or it isn't shared with Claude). Use a file_id from the other tools.")
    fmt = format_of(row["display_name"])
    if fmt is None:
        raise EditError(f"“{row['display_name']}” isn't a file Claude can open for editing: only "
                        f"{', '.join(sorted(EDITABLE))}. Use search_documents or read_passage to read it.")
    try:
        path = store.content_path(conn, settings, file_id)
        if path.stat().st_size > settings.edit_max_bytes:
            raise EditError(f"“{row['display_name']}” is over {settings.edit_max_bytes // (1024 * 1024)} MB, "
                            f"too big to edit here.")
        data = path.read_bytes()
    except share.ShareUnavailable as exc:
        raise EditConflict(str(exc)) from None
    except OSError:
        raise EditError("The file's content is missing on the server; tell a Mnemerynne admin.") from None
    return Loaded(row, path, data, sha256(data), fmt)


def _lock_files(path: Path) -> list[Path]:
    """Word and Excel mark an open document with ~$ + name (Word drops the
    first two letters of longer names)."""
    name = path.name
    return [path.with_name("~$" + name), path.with_name("~$" + name[2:])]


# ------------------------------------------------------------------ saving
def _keep_version(settings: Settings, file_id: int, data: bytes, ext: str) -> str:
    rel = f"{file_id}/{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}{ext}"
    dest = settings.data_dir / "versions" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "xb") as fh:
        fh.write(data)
    return rel


def version_path(settings: Settings, rel_path: str) -> Path:
    return settings.data_dir / "versions" / rel_path


def replace_content(conn: sqlite3.Connection, settings: Settings, file_id: int, data: bytes, *,
                    expected: str | None, actor_id: int, via: str, summary: str,
                    token_id: int | None = None, edited_via: str | None = None) -> str:
    """Give a file new content, keeping the old as a version. `expected` is the
    version the change was made from (None: whatever is there now). Returns
    the new version. Callers check permission first."""
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    on_share = share.is_share(conn, row["collection_id"])
    try:
        path = store.content_path(conn, settings, file_id)
        current = path.read_bytes()
    except share.ShareUnavailable as exc:
        raise EditConflict(str(exc)) from None
    current_sha = sha256(current)
    if expected is not None and current_sha != expected:
        raise EditConflict(f"“{row['display_name']}” changed since it was opened (someone else saved it). "
                           f"Open it again and redo the change on the new version.")
    if on_share and any(lock.exists() for lock in _lock_files(path)):
        raise EditConflict(f"“{row['display_name']}” is open in Word or Excel by someone right now. "
                           f"Try again once they've closed it.")
    if len(data) > settings.edit_max_bytes:
        raise EditError(f"The result would be over {settings.edit_max_bytes // (1024 * 1024)} MB.")
    new_sha = sha256(data)
    if new_sha == current_sha:
        return current_sha

    ext = PurePosixPath(row["display_name"]).suffix.lower()
    kept = _keep_version(settings, file_id, current, ext)
    # The new content goes to a temporary file first: in blob storage's temp
    # area, or next to the file on the share (a dot name, which the share sync
    # ignores) so the final rename stays on one filesystem.
    tmp_dir = path.parent if on_share else settings.tmp_dir
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f".mnemerynne-{secrets.token_hex(6)}.tmp"
    try:
        with open(tmp, "xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        kind, mime, size, note = store.inspect(tmp, row["display_name"], settings)
        with transaction(conn):
            conn.execute("INSERT INTO file_versions(file_id, sha256, size, rel_path, created_by, via, token_id, summary) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (file_id, current_sha, len(current), kept, actor_id, via, token_id, summary[:2000]))
            if on_share:
                conn.execute("INSERT OR IGNORE INTO blobs(sha256, kind, mime, size, rel_path) VALUES (?, ?, ?, ?, ?)",
                             (new_sha, kind, mime, size, store.blob_rel_path(new_sha, kind)))
            else:
                store.store(conn, settings, store.Received(new_sha, kind, size, tmp, row["display_name"], mime, note))
            kind = conn.execute("SELECT kind FROM blobs WHERE sha256 = ?", (new_sha,)).fetchone()["kind"]
            status = "stored" if kind == "other" else "queued"
            conn.execute("UPDATE files SET blob_sha256 = ?, status = ?, error = NULL, progress = NULL, notes = ?, "
                         "updated_at = datetime('now'), edited_via = ? WHERE id = ?",
                         (new_sha, status, note, edited_via, file_id))
            if status == "queued":
                jobs.enqueue(conn, "ingest", file_id)
            share._drop_unused_blob(conn, settings, row["blob_sha256"])
            if on_share:
                # Last: if the rename fails, the transaction rolls back and the
                # file on the share is still the old one.
                os.replace(tmp, path)
                share.record_stat(conn, file_id, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        version_path(settings, kept).unlink(missing_ok=True)
        raise
    return new_sha


def save(conn: sqlite3.Connection, settings: Settings, caller, file_id: int, data: bytes, *,
         expected: str, summary: str) -> str:
    """Claude's change to a file, after the permission check."""
    loaded = load(conn, settings, caller.user_id, file_id)
    check_edit(conn, caller.user_id, caller.can_edit, loaded.row["collection_id"], loaded.row["folder_id"])
    return replace_content(conn, settings, file_id, data, expected=expected, actor_id=caller.user_id,
                           via="claude", summary=summary, token_id=caller.token_id,
                           edited_via=f"claude:{caller.username}")


def create(conn: sqlite3.Connection, settings: Settings, caller, name: str, data: bytes,
           collection_id: int | None = None, folder_id: int | None = None) -> tuple[int, str]:
    """A new file from Claude; in the caller's Agents folder unless another
    place where Claude may edit is given. Returns (file_id, version)."""
    name = store.clean_display_name(name)
    if format_of(name) is None:
        raise EditError(f"Claude can create {', '.join(sorted(EDITABLE))} files; “{name}” is none of those.")
    if collection_id is None:
        try:
            folder_id = agents_folder(conn, caller.user_id)
        except fs.FsError as exc:
            raise EditConflict(str(exc)) from None
        collection_id = permissions.private_collection_id(conn, caller.user_id)
        if folder_id is None or collection_id is None:
            raise EditError("You have no inbox, so there's no Agents folder to put it in.")
    check_edit(conn, caller.user_id, caller.can_edit, collection_id, folder_id)
    if len(data) > settings.edit_max_bytes:
        raise EditError(f"The file would be over {settings.edit_max_bytes // (1024 * 1024)} MB.")
    try:
        received = store.receive(io.BytesIO(data), name, settings)
        file_id, _ = store.add_file(conn, settings, received, collection_id, caller.user_id, folder_id=folder_id)
    except (store.UploadError, fs.FsError) as exc:
        raise EditError(str(exc)) from None
    conn.execute("UPDATE files SET edited_via = ? WHERE id = ?", (f"claude:{caller.username}", file_id))
    return file_id, sha256(data)


# ---------------------------------------------------------------- versions
def versions(conn: sqlite3.Connection, file_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT v.id, v.sha256, v.size, v.created_at, v.via, v.summary, u.username AS by FROM file_versions v "
        "LEFT JOIN users u ON u.id = v.created_by WHERE v.file_id = ? ORDER BY v.id DESC", (file_id,)).fetchall()
    return [{"id": r["id"], "version": r["sha256"], "size": r["size"], "replaced_at": r["created_at"],
             "replaced_by": ("Claude for " if r["via"] == "claude" else "restore by ") + (r["by"] or "a deleted user"),
             "change": r["summary"]} for r in rows]


def version_row(conn: sqlite3.Connection, file_id: int, version_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM file_versions WHERE id = ? AND file_id = ?", (version_id, file_id)).fetchone()
    if row is None:
        raise EditNotFound("No such version.")
    return row


def restore(conn: sqlite3.Connection, settings: Settings, file_id: int, version_id: int, actor_id: int) -> str:
    """Put an earlier version back (the current content becomes a version too).
    The caller has checked that the person may change the file."""
    v = version_row(conn, file_id, version_id)
    try:
        data = version_path(settings, v["rel_path"]).read_bytes()
    except OSError:
        raise EditNotFound("That version's content is gone (it may have expired).") from None
    return replace_content(conn, settings, file_id, data, expected=None, actor_id=actor_id, via="restore",
                           summary=f"restored the version from {v['created_at']}", edited_via=None)


def purge_versions(conn: sqlite3.Connection, settings: Settings) -> int:
    """Drop versions older than version_days or beyond the newest version_keep
    per file, and leftovers of deleted files. Returns how many went."""
    cutoff = f"-{int(settings.version_days)} days"
    old = conn.execute(
        "SELECT id, rel_path FROM file_versions WHERE created_at < datetime('now', ?) OR id IN ("
        "  SELECT id FROM (SELECT id, row_number() OVER (PARTITION BY file_id ORDER BY id DESC) AS n "
        "                  FROM file_versions) WHERE n > ?)", (cutoff, settings.version_keep)).fetchall()
    for row in old:
        version_path(settings, row["rel_path"]).unlink(missing_ok=True)
        conn.execute("DELETE FROM file_versions WHERE id = ?", (row["id"],))
    base = settings.data_dir / "versions"
    if base.is_dir():
        live = {str(r[0]) for r in conn.execute("SELECT DISTINCT file_id FROM file_versions")}
        for entry in base.iterdir():
            if entry.is_dir() and entry.name not in live:
                shutil.rmtree(entry, ignore_errors=True)
    return len(old)
