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

Erynne (via="erynne", the agent in chat) follows the same rules except the
Claude sharing: she runs on this server, so she reads whatever the person can
read. Her changes only land when the person applies them (proposals.py).
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


ASSISTANTS = {"claude": "Claude", "erynne": "Erynne"}


def ai_access(conn: sqlite3.Connection, user_id: int, via: str = "claude") -> fs.Access:
    """What the assistant may read for this person: Claude only what is shared
    with Claude (its reads leave the building); Erynne all the person can read."""
    return fs.Access.for_claude(conn, user_id) if via == "claude" else fs.Access.of(conn, user_id)


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


def _chain_flag(conn: sqlite3.Connection, column: str, folder_id: int | None,
                file_id: int | None = None) -> str | None:
    """The name of the file, or of the nearest folder at or above folder_id, with `column` set; None if none."""
    if file_id is not None:
        row = conn.execute(f"SELECT display_name, {column} AS flag FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is not None and row["flag"]:
            return row["display_name"]
    if folder_id is None:
        return None
    row = conn.execute(
        f"WITH RECURSIVE up(id, parent_id, flag, name) AS ("
        f" SELECT id, parent_id, {column}, name FROM folders WHERE id = ? UNION ALL"
        f" SELECT f.id, f.parent_id, f.{column}, f.name FROM folders f JOIN up ON f.id = up.parent_id)"
        f" SELECT name FROM up WHERE flag = 1 LIMIT 1", (folder_id,)).fetchone()
    return row["name"] if row is not None else None


def no_edit_reason(conn: sqlite3.Connection, folder_id: int | None, file_id: int | None = None) -> str | None:
    """Why the AI may not change this file (or things in this folder): "AI can edit" switched off on the file
    or on a folder at or above `folder_id` (files.ai_no_edit, folders.ai_no_edit); None if nothing stops it."""
    name = _chain_flag(conn, "ai_no_edit", folder_id, file_id)
    return f"“{name}” is read-only for the AI (AI edit is off for it in Files)." if name else None


def no_edit_item(conn: sqlite3.Connection, folder_id: int | None, file_id: int | None = None) -> fs.Item | None:
    """The file, or the nearest folder at or above folder_id, whose "AI can edit" is switched off (the one
    no_edit_reason names); None if nothing is."""
    if file_id is not None and conn.execute("SELECT ai_no_edit FROM files WHERE id = ?", (file_id,)).fetchone()[0]:
        return fs.Item("file", file_id)
    if folder_id is None:
        return None
    row = conn.execute(
        "WITH RECURSIVE up(id, parent_id, flag, depth) AS ("
        " SELECT id, parent_id, ai_no_edit, 0 FROM folders WHERE id = ? UNION ALL"
        " SELECT f.id, f.parent_id, f.ai_no_edit, up.depth + 1 FROM folders f JOIN up ON f.id = up.parent_id)"
        " SELECT id FROM up WHERE flag = 1 ORDER BY depth LIMIT 1", (folder_id,)).fetchone()
    return fs.Item("folder", row["id"]) if row is not None else None


def owner_allows(conn: sqlite3.Connection, folder_id: int | None, file_id: int | None = None) -> bool:
    """The inbox owner switched "AI can edit" on for this file or a folder at or above folder_id."""
    return _chain_flag(conn, "ai_edit", folder_id, file_id) is not None


def check_edit(conn: sqlite3.Connection, user_id: int, token_can_edit: bool,
               collection_id: int, folder_id: int | None, *, via: str = "claude", file_id: int | None = None,
               item_flags: bool = True, ai: fs.Access | None = None, own: fs.Access | None = None) -> Place:
    """Raise unless the assistant (Claude, or Erynne) may change files at this
    place for this person. file_id: the file being changed (its own switches count too).
    item_flags=False leaves out the per-folder and per-file switches (to show them).
    ai, own: the assistant's and the person's Access, when the caller already has them (EditCheck)."""
    who = ASSISTANTS[via]
    if not (ai or ai_access(conn, user_id, via)).can_read(collection_id, folder_id):
        raise EditNotFound("No such place" + (" (or it isn't shared with Claude)." if via == "claude" else "."))
    if not token_can_edit:
        raise EditForbidden("This connection can only search. To let Claude change files, open Claude in "
                            "Mnemerynne and turn on “Let Claude edit files” for this token.")
    if not (own or fs.Access.of(conn, user_id)).can_write(collection_id, folder_id):
        raise EditForbidden(f"You can't change files here yourself, so {who} can't either.")
    if item_flags:
        why = no_edit_reason(conn, folder_id, file_id)
        if why:
            raise EditForbidden(why)
    coll = conn.execute("SELECT kind, owner_user_id, claude_edit FROM collections WHERE id = ?",
                        (collection_id,)).fetchone()
    if coll["claude_edit"]:
        return Place(collection_id, folder_id, "library")
    own_inbox = coll["kind"] == "private" and coll["owner_user_id"] == user_id
    if own_inbox and folder_id in _agents_subtree(conn, collection_id):
        return Place(collection_id, folder_id, "agents")
    if own_inbox and item_flags and owner_allows(conn, folder_id, file_id):
        return Place(collection_id, folder_id, "owner")
    if own_inbox:
        raise EditForbidden(f"{who} isn't allowed to change files in your File box outside the Agents folder. Work "
                            f"in your {AGENTS} folder instead (create_file puts new files there), or ask the person "
                            f"to turn on AI edit for this folder or file in Files.")
    raise EditForbidden(f"{who} isn't allowed to change files in this library. Work in your {AGENTS} folder "
                        f"instead (create_file puts new files there), or ask an admin to allow AI edits here.")


class EditCheck:
    """check_edit for many items at once (the `editable` flag on listings): the Access lookups are made once."""

    def __init__(self, conn: sqlite3.Connection, user_id: int, token_can_edit: bool, via: str = "claude"):
        self.conn, self.user_id, self.token_can_edit, self.via = conn, user_id, token_can_edit, via
        self.ai = ai_access(conn, user_id, via)
        self.own = fs.Access.of(conn, user_id)

    def allows(self, collection_id: int, folder_id: int | None, file_id: int | None = None) -> bool:
        """The assistant may change this file (file_id) or files in this folder."""
        if not self.token_can_edit:
            return False
        try:
            check_edit(self.conn, self.user_id, True, collection_id, folder_id, via=self.via, file_id=file_id,
                       ai=self.ai, own=self.own)
            return True
        except EditError:
            return False


def edit_state(conn: sqlite3.Connection, user_id: int, collection_id: int, place: int | None,
               above: int | None, own_edit: int, own_no_edit: int) -> dict:
    """The "AI can edit" switch of a file (place and above = its folder) or a folder (place = itself, above =
    its parent): {on, can_change, shared, why}. In their own inbox people can switch it on (like the Agents
    folder); elsewhere only an admin allows AI edits and the switch can only make things read-only."""
    access = fs.Access.of(conn, user_id)
    coll = conn.execute("SELECT kind, owner_user_id FROM collections WHERE id = ?", (collection_id,)).fetchone()
    grantable = bool(coll) and coll["kind"] == "private" and coll["owner_user_id"] == user_id \
        and access.can_write(collection_id, place)
    try:
        check_edit(conn, user_id, True, collection_id, place, via="erynne", item_flags=False)
        base, why = True, None
    except EditForbidden as exc:
        base, why = False, str(exc)
    except EditError as exc:
        base, why, grantable = False, str(exc), False
    blocked = no_edit_reason(conn, above)
    if blocked:
        return {"on": False, "can_change": False, "shared": False, "why": blocked}
    allowed_above = base or (grantable and owner_allows(conn, above))
    shared = allowed_above or grantable
    on = not own_no_edit and (allowed_above or (grantable and bool(own_edit)))
    return {"on": on, "can_change": shared and access.can_write(collection_id, place), "shared": shared,
            "why": None if shared else why}


def editable_places(conn: sqlite3.Connection, user_id: int, via: str = "claude") -> list[dict]:
    """Where the assistant may edit for this person (ignoring the token), for display."""
    out = []
    try:
        agents = agents_folder(conn, user_id)
    except (fs.FsError, OSError):
        agents = None   # the share is away; it's made next time
    if agents is not None:
        out.append({"library": permissions.PRIVATE_LABEL, "folder": AGENTS, "why": "your Agents folder"})
    access = fs.Access.of(conn, user_id)
    ai = ai_access(conn, user_id, via)
    for row in conn.execute("SELECT id, kind, name FROM collections WHERE claude_edit = 1").fetchall():
        reach = access.reaches.get(row["id"])
        if reach is None or not reach.any_write or row["id"] not in ai.readable:
            continue
        out.append({"library": permissions.PRIVATE_LABEL if row["kind"] == "private" else row["name"],
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


def load(conn: sqlite3.Connection, settings: Settings, user_id: int, file_id: int, *,
         via: str = "claude") -> Loaded:
    """A file the assistant may read, with its current bytes and version."""
    row = fs.file_row(conn, file_id)
    if row is None or row["deleted_at"] is not None or not fs.is_live_folder(conn, row["folder_id"]) \
            or not ai_access(conn, user_id, via).can_read_file(row):
        raise EditNotFound("No such file" + (" (or it isn't shared with Claude)" if via == "claude" else "")
                           + ". Use a file_id from the other tools.")
    fmt = format_of(row["display_name"])
    if fmt is None:
        raise EditError(f"“{row['display_name']}” isn't a file {ASSISTANTS[via]} can open for editing: only "
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
        try:
            with open(tmp, "xb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            if not on_share:
                raise
            raise EditError(str(share._fail("save the file", exc))) from exc
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
                try:
                    os.replace(tmp, path)
                except OSError as exc:
                    raise EditError(str(share._fail("save the file", exc))) from exc
                share.record_stat(conn, file_id, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        version_path(settings, kept).unlink(missing_ok=True)
        raise
    return new_sha


def save(conn: sqlite3.Connection, settings: Settings, caller, file_id: int, data: bytes, *,
         expected: str, summary: str, via: str = "claude") -> str:
    """The assistant's change to a file, after the permission check."""
    loaded = load(conn, settings, caller.user_id, file_id, via=via)
    check_edit(conn, caller.user_id, caller.can_edit, loaded.row["collection_id"], loaded.row["folder_id"], via=via,
               file_id=file_id)
    return replace_content(conn, settings, file_id, data, expected=expected, actor_id=caller.user_id,
                           via=via, summary=summary, token_id=caller.token_id,
                           edited_via=f"{via}:{caller.username}")


def new_file_place(conn: sqlite3.Connection, user_id: int, collection_id: int | None,
                   folder_id: int | None) -> tuple[int, int | None]:
    """Where a new file goes: the given place, or the person's Agents folder."""
    if collection_id is not None:
        return collection_id, folder_id
    try:
        folder_id = agents_folder(conn, user_id)
    except fs.FsError as exc:
        raise EditConflict(str(exc)) from None
    collection_id = permissions.private_collection_id(conn, user_id)
    if folder_id is None or collection_id is None:
        raise EditError("You have no File box, so there's no Agents folder to put it in.")
    return collection_id, folder_id


def create(conn: sqlite3.Connection, settings: Settings, caller, name: str, data: bytes,
           collection_id: int | None = None, folder_id: int | None = None, *, via: str = "claude",
           raw: bool = False) -> tuple[int, str]:
    """A new file from the assistant; in the caller's Agents folder unless
    another place where it may edit is given. Returns (file_id, version).
    raw: the bytes are the file as the assistant made it (an image, a PDF, a
    zip), so any kind is accepted under the same rules as a browser upload."""
    name = store.clean_display_name(name)
    if not raw and format_of(name) is None:
        raise EditError(f"{ASSISTANTS[via]} can create {', '.join(sorted(EDITABLE))} files; "
                        f"“{name}” is none of those.")
    collection_id, folder_id = new_file_place(conn, caller.user_id, collection_id, folder_id)
    check_edit(conn, caller.user_id, caller.can_edit, collection_id, folder_id, via=via)
    if len(data) > settings.edit_max_bytes:
        raise EditError(f"The file would be over {settings.edit_max_bytes // (1024 * 1024)} MB.")
    try:
        received = store.receive(io.BytesIO(data), name, settings)
        file_id, _ = store.add_file(conn, settings, received, collection_id, caller.user_id, folder_id=folder_id)
    except (store.UploadError, fs.FsError) as exc:
        raise EditError(str(exc)) from None
    conn.execute("UPDATE files SET edited_via = ? WHERE id = ?", (f"{via}:{caller.username}", file_id))
    return file_id, sha256(data)


def check_item_edit(conn: sqlite3.Connection, caller, item: fs.Item, *, via: str = "claude") -> sqlite3.Row:
    """Raise unless the assistant may rename, move or bin this file or folder for the caller: it may edit
    where the item sits and the item itself, and for a folder nothing inside is read-only for the AI."""
    who = ASSISTANTS[via]
    if item.type == "file":
        row = conn.execute("SELECT * FROM files WHERE id = ? AND deleted_at IS NULL", (item.id,)).fetchone()
        if row is None or not fs.is_live_folder(conn, row["folder_id"]) or \
                not ai_access(conn, caller.user_id, via).can_read_file(row):
            raise EditNotFound("No such file" + (" (or it isn't shared with Claude)." if via == "claude" else "."))
        check_edit(conn, caller.user_id, caller.can_edit, row["collection_id"], row["folder_id"], via=via,
                   file_id=item.id)
        return row
    row = conn.execute("SELECT * FROM folders WHERE id = ? AND deleted_at IS NULL", (item.id,)).fetchone()
    if row is None or not fs.is_live_folder(conn, item.id):
        raise EditNotFound("No such folder.")
    if row["role"]:
        raise EditForbidden(f"“{row['name']}” is the {row['role'].capitalize()} folder; it stays as it is.")
    check_edit(conn, caller.user_id, caller.can_edit, row["collection_id"], row["parent_id"], via=via)
    check_edit(conn, caller.user_id, caller.can_edit, row["collection_id"], item.id, via=via)
    below = fs.subtree(conn, item.id)
    marks = ",".join("?" * len(below))
    locked = conn.execute(
        f"SELECT name FROM folders WHERE id IN ({marks}) AND ai_no_edit = 1 UNION ALL "
        f"SELECT display_name FROM files WHERE folder_id IN ({marks}) AND ai_no_edit = 1 AND deleted_at IS NULL "
        f"LIMIT 1", (*below, *below)).fetchone()
    if locked:
        raise EditForbidden(f"“{locked[0]}” inside it is read-only for the AI, so {who} can't move or delete "
                            f"the folder.")
    return row


def rename_item(conn: sqlite3.Connection, caller, item: fs.Item, name: str, *, via: str = "claude") -> str:
    """Rename a file or folder where the assistant may edit; a file keeps its extension. Returns the name."""
    if "/" in (name or "") or "\\" in (name or ""):
        raise EditError("Give just the new name, without / or \\; use move_item to put it somewhere else.")
    row = check_item_edit(conn, caller, item, via=via)
    if item.type == "file":
        old, new = Path(row["display_name"]).suffix.lower(), Path(name.strip()).suffix.lower()
        if old != new:
            raise EditError(f"Keep the extension: “{row['display_name']}” is a {old or 'no-extension'} file.")
    try:
        new = fs.rename(conn, fs.Access.of(conn, caller.user_id), item, name)
    except fs.FsError as exc:
        raise EditError(str(exc)) from None
    _credit(conn, caller, item, via)
    return new


def _credit(conn: sqlite3.Connection, caller, item: fs.Item, via: str) -> None:
    """A file the assistant renamed or moved: list_changes says who did it, not "someone in Files". Kept
    apart from edited_via (who changed the content); it counts while moved_at is still the file's updated_at."""
    if item.type == "file":
        conn.execute("UPDATE files SET moved_via = ?, moved_at = updated_at WHERE id = ?",
                     (f"{via}:{caller.username}", item.id))


def move_item(conn: sqlite3.Connection, caller, item: fs.Item, collection_id: int | None, folder_id: int | None,
              *, via: str = "claude") -> str:
    """Move a file or folder from one place where the assistant may edit to another (the Agents folder
    when no place is given). Returns its name there, which gets " (2)" if the name was taken."""
    check_item_edit(conn, caller, item, via=via)
    collection_id, folder_id = new_file_place(conn, caller.user_id, collection_id, folder_id)
    check_edit(conn, caller.user_id, caller.can_edit, collection_id, folder_id, via=via)
    try:
        result = fs.move(conn, fs.Access.of(conn, caller.user_id), [item], collection_id, folder_id)[0]
    except fs.FsError as exc:
        raise EditError(str(exc)) from None
    if not result.ok:
        raise {404: EditNotFound, 403: EditForbidden, 409: EditConflict}.get(result.status, EditError)(
            result.error or "It couldn't be moved.")
    _credit(conn, caller, item, via)
    return result.extra["name"]


def create_folder(conn: sqlite3.Connection, caller, name: str, collection_id: int | None = None,
                  parent_id: int | None = None, *, via: str = "claude") -> tuple[int, bool]:
    """A new folder from the assistant, inside a place where it may edit (the caller's Agents folder
    unless another is given). Returns (folder_id, created); a folder of that name already there is
    returned as it is, so asking twice is harmless."""
    if "/" in (name or "") or "\\" in (name or ""):
        # The name cleaner would keep only the last part; say so instead of making something else.
        raise EditError("Give one folder name, without / or \\. To make a folder inside another, name the "
                        "outer one as `folder`.")
    collection_id, parent_id = new_file_place(conn, caller.user_id, collection_id, parent_id)
    check_edit(conn, caller.user_id, caller.can_edit, collection_id, parent_id, via=via)
    try:
        name = fs.clean_name(name)
        return fs.create_folder(conn, fs.Access.of(conn, caller.user_id), collection_id, parent_id, name), True
    except fs.Conflict:
        row = conn.execute("SELECT id FROM folders WHERE collection_id = ? AND COALESCE(parent_id, 0) = ? "
                           "AND lower(name) = lower(?) AND deleted_at IS NULL",
                           (collection_id, parent_id or 0, name)).fetchone()
        return row["id"], False
    except fs.FsError as exc:
        raise EditError(str(exc)) from None


# ---------------------------------------------------------------- versions
def versions(conn: sqlite3.Connection, file_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT v.id, v.sha256, v.size, v.created_at, v.via, v.summary, u.username AS by FROM file_versions v "
        "LEFT JOIN users u ON u.id = v.created_by WHERE v.file_id = ? ORDER BY v.id DESC", (file_id,)).fetchall()
    return [{"id": r["id"], "version": r["sha256"], "size": r["size"], "replaced_at": r["created_at"],
             "replaced_by": {"claude": "Claude for ", "erynne": "Erynne for ", "web": ""}.get(r["via"], "restore by ")
                            + (r["by"] or "a deleted user"),
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
