"""The desktop app's routes (clients/desktop-app; mnemerynne/desktop.py, mnemerynne/drive.py).

- /api/desktop/info: before signing in, the name, icon and newest build of this server (no auth).
- /downloads/desktop/<installer>: the installer, for people in the Desktop app group (a page or the app itself).
- /api/desktop/session-ticket + /desktop/signin: sign the app's tabs in again without a password.
- /api/drive/*: the Explorer drive. Listing, downloading, saving, adding, renaming, moving and binning files,
  always through fs.Access, so the drive can do exactly what the person can do in Files and no more.

Everything but the info and the sign-in page needs an OAuth token with the scope 'desktop' (oauth.py), from a
person still in the Desktop app group. Those tokens work nowhere else, and other tokens don't work here.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from mnemerynne import db, desktop, drive, editing, fs, identity, oauth, share, users
from mnemerynne.answer import ASSISTANT_NAME
from mnemerynne.api_tokens import Caller
from mnemerynne.config import Settings
from mnemerynne.ingest import store
from mnemerynne.web.deps import _session_user, get_db, get_settings
from mnemerynne.web.front import _safe_return
from mnemerynne.web.sendfile import send_file

router = APIRouter()

_BLOCK = 1024 * 1024        # bytes written per step when saving an upload to disk


class DesktopCaller:
    """The person behind a desktop token, and the token's connection (for the version history)."""

    def __init__(self, user: sqlite3.Row, caller: Caller):
        self.user, self.caller = user, caller

    @property
    def id(self) -> int:
        return self.user["id"]


def desktop_caller(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> DesktopCaller:
    """The person behind a 'desktop' OAuth token, while they're active and in the Desktop app group."""
    auth = request.headers.get("authorization", "")
    value = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    caller = oauth.verify(conn, value, scope="desktop")
    user = users.get_user_by_id(conn, caller.user_id) if caller else None
    if user is None or not user["active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in; sign the desktop app in again.",
                            headers={"WWW-Authenticate": "Bearer"})
    if not desktop.allowed(conn, user, request.app.state.directory):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The desktop app isn't open for you; ask an admin.")
    return DesktopCaller(user, caller)


def _base(request: Request, settings: Settings) -> str:
    return (settings.public_url or str(request.base_url)).rstrip("/")


def _fs_errors(fn):
    """Run fn(), turning file-manager and editing refusals into HTTP errors (as web/files.py does)."""
    try:
        return fn()
    except fs.FsError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except editing.EditConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except editing.EditError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except store.UploadError as exc:
        code = status.HTTP_413_CONTENT_TOO_LARGE if isinstance(exc, store.TooLarge) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, str(exc)) from exc
    except share.ShareError as exc:                    # e.g. the share isn't mounted
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


# ---------------------------------------------------------------- the app itself
@router.get("/api/desktop/info")
def info(request: Request, conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """What the app shows before signing in, and checks for updates: this server's name and icon, and the newest
    installer (its version, where to get it, and its SHA-256 to check the download against)."""
    base = _base(request, settings)
    me = identity.load(conn, settings)
    build = desktop.latest_build(settings)
    return {"product_name": me.name or ASSISTANT_NAME, "server": base,
            "icon": me.mark_url or f"{base}/static/icons/icon-192.png",
            "enabled": bool(desktop.group(conn)),
            "latest": build["version"] if build else None,
            "download": ({"url": f"{base}/downloads/desktop/{build['name']}", "name": build["name"],
                          "size": build["size"], "sha256": build["sha256"]} if build else None)}


@router.get("/api/desktop/me")
def me(who: DesktopCaller = Depends(desktop_caller)):
    """Who the app is signed in as (its Settings page shows it)."""
    return {"id": who.id, "username": who.user["username"], "display_name": who.user["display_name"]
            if "display_name" in who.user.keys() else who.user["username"]}


@router.get("/downloads/desktop/{name}")
def download_build(name: str, request: Request, conn: sqlite3.Connection = Depends(get_db),
                   settings: Settings = Depends(get_settings)):
    """The installer, for people in the Desktop app group: from Preferences (signed in) or from the app updating
    itself (its token)."""
    user = _session_user(request, conn)
    if user is None:
        user = desktop_caller(request, conn).user    # 401/403 if there's no good token either
    elif not desktop.allowed(conn, user, request.app.state.directory):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The desktop app isn't open for you; ask an admin.")
    path = desktop.build_file(settings, name)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such build")
    return send_file(path, media_type="application/vnd.microsoft.portable-executable", filename=name,
                     content_disposition_type="attachment", headers={"X-Content-Type-Options": "nosniff"})


@router.post("/api/desktop/session-ticket")
def session_ticket(request: Request, who: DesktopCaller = Depends(desktop_caller),
                   conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """A one-minute, single-use ticket: the app opens `signin_url` in a tab that landed on the sign-in page."""
    ticket = desktop.issue_ticket(conn, who.id, who.caller.grant_id)
    return {"ticket": ticket, "signin_url": f"{_base(request, settings)}/desktop/signin?t={ticket}",
            "expires_in": desktop.TICKET_SECONDS}


@router.get("/desktop/signin")
def ticket_signin(request: Request, t: str = Query("", max_length=200), next: str = Query("/", max_length=2000),
                  conn: sqlite3.Connection = Depends(get_db)):
    """Sign this tab in with a ticket, the same as the sign-in form would, then go on to `next` (this site only)."""
    user_id = desktop.use_ticket(conn, t)
    user = users.get_user_by_id(conn, user_id) if user_id else None
    if user is None or not desktop.allowed(conn, user, request.app.state.directory):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)   # the normal sign-in instead
    request.session.clear()                            # as /login does: a fresh session for this person
    request.session["user_id"] = user["id"]
    request.session["epoch"] = user["session_epoch"]
    return RedirectResponse(next if _safe_return(next) else "/", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------- Preferences → Desktop app
def _page_member(request: Request, conn: sqlite3.Connection) -> sqlite3.Row:
    """The signed-in person, if they're in the Desktop app group (Preferences' card)."""
    user = _session_user(request, conn)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if not desktop.allowed(conn, user, request.app.state.directory):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The desktop app isn't open for you; ask an admin.")
    return user


@router.get("/api/desktop/connections")
def connections(request: Request, conn: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    """The card: the installer to download, and the computers where the app is signed in as this person."""
    user = _page_member(request, conn)
    build = desktop.latest_build(settings)
    return {"latest": build["version"] if build else None,
            "download": f"/downloads/desktop/{build['name']}" if build else None,
            "size": build["size"] if build else None,
            "computers": oauth.connections(conn, user["id"], scope="desktop")}


@router.delete("/api/desktop/connections/{grant_id}")
def sign_out_computer(grant_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Sign the app out on one computer: its tokens stop working at once (its drive stops syncing)."""
    user = _session_user(request, conn)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    row = conn.execute("SELECT scope FROM oauth_grants WHERE id = ? AND user_id = ?", (grant_id, user["id"])).fetchone()
    if row is None or row["scope"] != "desktop" or not oauth.revoke_grant(conn, grant_id, user["id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return {"status": "signed out", "id": grant_id}


# -------------------------------------------------------------------- the drive
@router.get("/api/drive/libraries")
def drive_libraries(who: DesktopCaller = Depends(desktop_caller), conn: sqlite3.Connection = Depends(get_db)):
    """Libraries this person may put on the drive (the app shows them as checkboxes)."""
    return {"libraries": drive.libraries(conn, fs.Access.of(conn, who.id)), "cursor": drive.cursor_now(conn)}


@router.get("/api/drive/list")
def drive_list(collection_id: int, folder_id: int | None = None, who: DesktopCaller = Depends(desktop_caller),
               conn: sqlite3.Connection = Depends(get_db)):
    """One folder (none: the library's top) when Explorer first opens it."""
    return _fs_errors(lambda: drive.listing(conn, fs.Access.of(conn, who.id), collection_id, folder_id))


@router.get("/api/drive/changes")
def drive_changes(collection_id: int, cursor: int = Query(0, ge=0), who: DesktopCaller = Depends(desktop_caller),
                  conn: sqlite3.Connection = Depends(get_db)):
    """What changed in a library after `cursor` (drive.changes)."""
    return _fs_errors(lambda: drive.changes(conn, fs.Access.of(conn, who.id), collection_id, cursor))


def _readable(conn: sqlite3.Connection, access: fs.Access, file_id: int) -> sqlite3.Row:
    row = fs.file_row(conn, file_id)
    # Same 404 whether it doesn't exist, is in the bin, or isn't theirs: no existence leaks.
    if row is None or row["deleted_at"] is not None or not access.can_read_file(row):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return row


@router.get("/api/drive/files/{file_id}/content")
def drive_download(file_id: int, who: DesktopCaller = Depends(desktop_caller),
                   conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """A file's bytes (Range requests work: Explorer fetches the parts a program reads). The ETag is the
    content's SHA-256, the version the app sends back in If-Match when it saves."""
    row = _readable(conn, fs.Access.of(conn, who.id), file_id)
    sha = conn.execute("SELECT blob_sha256 FROM files WHERE id = ?", (file_id,)).fetchone()[0]
    path = _fs_errors(lambda: store.content_path(conn, settings, file_id))
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File content is missing")
    return send_file(path, media_type="application/octet-stream", filename=row["display_name"],
                     content_disposition_type="attachment",
                     headers={"X-Content-Type-Options": "nosniff", "ETag": f'"{sha}"'})


async def _receive_body(request: Request, settings: Settings, into: Path | None) -> tuple[Path, str]:
    """Write the request body to a new temp file (in `into`, a share folder, else the temp area), hashing it as it
    goes and holding it to max_file_bytes. Returns (path, sha256); the caller owns the file."""
    if into is not None:
        path, fh = share.open_stage(into)              # a hidden file in its share folder: no second copy later
    else:
        settings.tmp_dir.mkdir(parents=True, exist_ok=True)
        path = settings.tmp_dir / f"drive-{secrets.token_hex(8)}.part"
        fh = open(path, "xb")
    h, size = hashlib.sha256(), 0
    try:
        with fh:
            buffer = bytearray()
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.max_file_bytes:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                                        f"Files can be at most {settings.max_file_bytes // (1024 * 1024)} MB.")
                h.update(chunk)
                buffer += chunk
                if len(buffer) >= _BLOCK:              # written a block at a time, off the event loop
                    await anyio.to_thread.run_sync(fh.write, bytes(buffer))
                    buffer.clear()
            if buffer:
                await anyio.to_thread.run_sync(fh.write, bytes(buffer))
    except BaseException:
        path.unlink(missing_ok=True)                   # never leave half a file behind
        raise
    return path, h.hexdigest()


def _strip_etag(value: str | None) -> str | None:
    value = (value or "").strip()
    if value.startswith("W/"):
        value = value[2:]
    return value.strip('"').lower() or None


@router.put("/api/drive/files/{file_id}/content")
async def drive_save(file_id: int, request: Request, who: DesktopCaller = Depends(desktop_caller),
                     settings: Settings = Depends(get_settings)):
    """Save new content from the drive (a program saved the file in Explorer). Send `If-Match` with the version
    (SHA-256) the app last had: if someone changed it on the server since, it's refused with 409 and the app keeps
    both. The old content is kept as a version ("via drive"), the same as any edit."""
    expected = _strip_etag(request.headers.get("if-match"))
    if expected is None:
        raise HTTPException(status.HTTP_428_PRECONDITION_REQUIRED, "Send If-Match with the version you last saw.")

    def check() -> None:                               # refuse before reading a byte of a big upload
        conn = db.connect(settings.db_path)
        try:
            access = fs.Access.of(conn, who.id)
            row = _readable(conn, access, file_id)
            if not access.can_write_file(row):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change files here")
        finally:
            conn.close()

    await anyio.to_thread.run_sync(check)
    src, sha = await _receive_body(request, settings, None)

    def save() -> dict:
        conn = db.connect(settings.db_path)            # off the event loop, with its own connection
        try:
            access = fs.Access.of(conn, who.id)
            row = _readable(conn, access, file_id)     # asked again: the upload may have taken minutes
            if not access.can_write_file(row):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change files here")
            version = _fs_errors(lambda: editing.replace_from_path(
                conn, settings, file_id, src, sha, expected=expected, actor_id=who.id, via="drive",
                summary="Saved from the desktop drive", edited_via=f"drive:{who.user['username']}"))
            row = fs.file_row(conn, file_id)
            return {"id": file_id, "sha256": version, "size": row["size"], "modified": row["updated_at"]}
        finally:
            src.unlink(missing_ok=True)                # replace_from_path consumed it; this covers refusals before
            conn.close()

    return await anyio.to_thread.run_sync(save)


@router.post("/api/drive/files")
async def drive_new_file(request: Request, collection_id: int, name: str = Query(..., min_length=1, max_length=255),
                         folder_id: int | None = None, who: DesktopCaller = Depends(desktop_caller),
                         settings: Settings = Depends(get_settings)):
    """A new file made in Explorer: the body is its bytes. Answers with the file as the server named it (a name
    already taken gets a number, as uploads in Files do)."""

    def place() -> Path | None:
        conn = db.connect(settings.db_path)
        try:
            access = fs.Access.of(conn, who.id)
            _fs_errors(lambda: access.write(collection_id, folder_id))
            _fs_errors(lambda: fs.require_folder(conn, collection_id, folder_id))
            return store.landing(conn, collection_id, folder_id, None, who.id)[1]
        finally:
            conn.close()

    into = await anyio.to_thread.run_sync(place)
    src, _sha = await _receive_body(request, settings, into)

    def add() -> dict:
        conn = db.connect(settings.db_path)
        try:
            access = fs.Access.of(conn, who.id)
            _fs_errors(lambda: access.write(collection_id, folder_id))   # still allowed after the upload?
            received = _fs_errors(lambda: store.received_from(src, name, settings, staged=into is not None))
            file_id, created = _fs_errors(lambda: store.add_file(conn, settings, received, collection_id, who.id,
                                                                  folder_id=folder_id))
            row = fs.file_row(conn, file_id)
            sha = conn.execute("SELECT blob_sha256 FROM files WHERE id = ?", (file_id,)).fetchone()[0]
            return JSONResponse({**drive._file_entry(row, sha, access), "created": created},
                                status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
        finally:
            src.unlink(missing_ok=True)                # gone already when it was kept
            conn.close()

    return await anyio.to_thread.run_sync(add)


class NewFolderBody(BaseModel):
    collection_id: int
    parent_id: int | None = None
    name: str = Field(..., min_length=1, max_length=200)


@router.post("/api/drive/folders", status_code=status.HTTP_201_CREATED)
def drive_new_folder(body: NewFolderBody, who: DesktopCaller = Depends(desktop_caller),
                     conn: sqlite3.Connection = Depends(get_db)):
    """A folder made in Explorer."""
    access = fs.Access.of(conn, who.id)
    folder_id = _fs_errors(lambda: fs.create_folder(conn, access, body.collection_id, body.parent_id, body.name))
    row = conn.execute("SELECT name FROM folders WHERE id = ?", (folder_id,)).fetchone()
    return {"type": "folder", "id": folder_id, "parent_id": body.parent_id, "name": row["name"]}


class RenameBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class MoveBody(BaseModel):
    collection_id: int
    folder_id: int | None = None


def _item(kind: str, item_id: int) -> fs.Item:
    if kind not in ("files", "folders"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return fs.Item("file" if kind == "files" else "folder", item_id)


@router.patch("/api/drive/{kind}/{item_id}")
def drive_rename(kind: str, item_id: int, body: RenameBody, who: DesktopCaller = Depends(desktop_caller),
                 conn: sqlite3.Connection = Depends(get_db)):
    """Renamed in Explorer. Answers the name the server kept (the same, unless it had to be cleaned up)."""
    access = fs.Access.of(conn, who.id)
    return {"id": item_id, "name": _fs_errors(lambda: fs.rename(conn, access, _item(kind, item_id), body.name))}


@router.post("/api/drive/{kind}/{item_id}/move")
def drive_move(kind: str, item_id: int, body: MoveBody, who: DesktopCaller = Depends(desktop_caller),
               conn: sqlite3.Connection = Depends(get_db)):
    """Moved in Explorer (within one library, or between two libraries on the drive)."""
    access = fs.Access.of(conn, who.id)
    result = _fs_errors(lambda: fs.move(conn, access, [_item(kind, item_id)], body.collection_id, body.folder_id))[0]
    if not result.ok:
        raise HTTPException(result.status, result.error)
    return result.json()


@router.delete("/api/drive/{kind}/{item_id}")
def drive_trash(kind: str, item_id: int, who: DesktopCaller = Depends(desktop_caller),
                conn: sqlite3.Connection = Depends(get_db)):
    """Deleted in Explorer: to the recycle bin, never for good (it can be put back from Files)."""
    result = fs.trash(conn, fs.Access.of(conn, who.id), [_item(kind, item_id)])[0]
    if not result.ok:
        raise HTTPException(result.status, result.error)
    return {"status": "trashed", "id": item_id}
