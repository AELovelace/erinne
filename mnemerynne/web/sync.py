"""The desktop backup app's API (/api/sync/*, mnemerynne/sync.py), its tokens, and the admin's Backups settings.

The app sends a sync token (`Authorization: Bearer mnemerynne_…`, scope 'sync'); those work only here, and MCP
tokens don't work here. Every call checks that Backups is on and the person is in its group.
"""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from mnemerynne import api_tokens, db, group_refs, oauth, sync, users
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import admin_user, api_user, get_db, get_settings, page_user

router = APIRouter()
admin_router = APIRouter()

TOKEN_DAYS = 365


class Caller:
    def __init__(self, user: sqlite3.Row, token_id: int | None):
        self.user, self.token_id = user, token_id

    @property
    def id(self) -> int:
        return self.user["id"]


def sync_caller(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Caller:
    """The person behind a sync token, while Backups is on and they're in its group."""
    auth = request.headers.get("authorization", "")
    value = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    caller = api_tokens.verify(conn, value, scope="sync") or oauth.verify(conn, value, scope="sync")
    user = users.get_user_by_id(conn, caller.user_id) if caller else None
    if user is None or not user["active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in; sign the backup app in again.",
                            headers={"WWW-Authenticate": "Bearer"})
    if not sync.may_backup(conn, user, request.app.state.directory):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Backups aren't open for you; ask an admin.")
    return Caller(user, caller.token_id)


def _root(conn: sqlite3.Connection, caller: Caller, root_id: int) -> sync.Root:
    try:
        return sync.get_root(conn, caller.id, root_id)
    except sync.SyncError as exc:
        raise HTTPException(exc.status, str(exc)) from None


def _refuse(exc: sync.SyncError) -> HTTPException:
    headers = {"ETag": f'"{exc.current}"'} if isinstance(exc, sync.Precondition) and exc.current else None
    return HTTPException(exc.status, str(exc), headers=headers)


# ------------------------------------------------------------------- client
@router.get("/api/sync/client")
def client_info(request: Request, settings: Settings = Depends(get_settings)):
    """For the backup app, before it signs in: where backups go, and the newest app (it offers to update)."""
    base = (settings.public_url or str(request.base_url)).rstrip("/")
    builds = sync.client_downloads(settings)
    return {"api_url": (settings.backup_url or base).rstrip("/"), "server": base, "latest": builds["latest"],
            "downloads": {kind: f"{base}/downloads/backup/{name}" for kind, name in builds["files"].items()}}


# ------------------------------------------------------------------- tokens
class TokenBody(BaseModel):
    computer: str = Field(..., min_length=1, max_length=80)


@router.get("/api/sync/tokens")
def list_tokens(user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """The person's backup app sign-ins (one per computer)."""
    return {"tokens": api_tokens.list_for_user(conn, user["id"], scope="sync")}


@router.post("/api/sync/tokens", status_code=status.HTTP_201_CREATED)
def create_token(body: TokenBody, request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """A sync token for the backup app on one computer (shown once)."""
    if not sync.may_backup(conn, user, request.app.state.directory):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Backups aren't open for you; ask an admin.")
    try:
        computer = sync.clean_part(body.computer.strip(), "computer name")
    except sync.SyncError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    with transaction(conn):
        token_id, token = api_tokens.create(conn, user["id"], f"Backup on {computer}", TOKEN_DAYS, scope="sync")
        _log(conn, user, "backup.token", user["username"], computer)
    return {"id": token_id, "token": token, "expires_in_days": TOKEN_DAYS}


@router.delete("/api/sync/tokens/{token_id}")
def revoke_token(token_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    if not any(t["id"] == token_id for t in api_tokens.list_for_user(conn, user["id"], scope="sync")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such sign-in.")
    api_tokens.revoke(conn, token_id, user["id"])
    return {"status": "revoked"}


# -------------------------------------------------------------------- roots
class RootBody(BaseModel):
    computer: str = Field(..., min_length=1, max_length=80)
    label: str = Field(..., min_length=1, max_length=80)


@router.get("/api/sync/roots")
def list_roots(caller: Caller = Depends(sync_caller), conn: sqlite3.Connection = Depends(get_db)):
    return {"roots": sync.roots(conn, caller.id)}


@router.post("/api/sync/roots")
def add_root(body: RootBody, caller: Caller = Depends(sync_caller), conn: sqlite3.Connection = Depends(get_db)):
    """Back up a folder: Backups\\<computer>\\<label>. The same root again if it exists."""
    try:
        root = sync.register(conn, caller.id, body.computer, body.label)
    except sync.SyncError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    _log(conn, caller.user, "backup.root", caller.user["username"], f"{root.computer}\\{root.label}")
    return next(r for r in sync.roots(conn, caller.id) if r["id"] == root.id)


class RootChange(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=80)
    searchable: bool | None = None


def _change_root(conn: sqlite3.Connection, user: sqlite3.Row, root_id: int, body: RootChange) -> dict:
    try:
        root = sync.get_root(conn, user["id"], root_id)
        if body.label is not None:
            root = sync.rename_root(conn, root, body.label)
        if body.searchable is not None:
            sync.set_searchable(conn, root, body.searchable)
            _log(conn, user, "backup.searchable", user["username"],
                 f"{root.computer}\\{root.label}: {'on' if body.searchable else 'off'}")
    except sync.SyncError as exc:
        raise _refuse(exc) from None
    return next(r for r in sync.roots(conn, user["id"]) if r["id"] == root_id)


@router.patch("/api/sync/roots/{root_id}")
def change_root(root_id: int, body: RootChange, caller: Caller = Depends(sync_caller),
                conn: sqlite3.Connection = Depends(get_db)):
    """From the app: a new label, or Searchable on or off."""
    return _change_root(conn, caller.user, root_id, body)


@router.get("/api/sync/roots/{root_id}/manifest")
def root_manifest(root_id: int, after: int = Query(0, ge=0), limit: int = Query(sync.MANIFEST_PAGE, ge=1),
                  caller: Caller = Depends(sync_caller), conn: sqlite3.Connection = Depends(get_db)):
    return sync.manifest(conn, _root(conn, caller, root_id), after, limit)


# ------------------------------------------------------------------- writes
def _mtime(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "X-Mtime is seconds since 1970, like 1761750000.5") from None


def _claimed_sha(request: Request) -> str:
    claimed = (request.headers.get("x-sha256") or "").strip().lower()
    if len(claimed) != 64 or any(c not in "0123456789abcdef" for c in claimed):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send X-Sha256: the SHA-256 of the body in hex.")
    return claimed


async def _receive(request: Request, settings: Settings, limit: int, claimed: str) -> Path:
    """The request body in a temp file, at most `limit` bytes and matching its X-Sha256."""
    too_big = HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                            f"At most {limit // (1024 * 1024)} MB per request; bigger files go up in parts.")
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit:
        raise too_big
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=settings.tmp_dir, suffix=".sync")
    upload = Path(name)
    digest, size = hashlib.sha256(), 0
    try:
        with open(fd, "wb") as out:
            async for block in request.stream():
                size += len(block)
                if size > limit:
                    raise too_big
                digest.update(block)
                out.write(block)
        if digest.hexdigest() != claimed:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The body doesn't match its X-Sha256; send it again.")
    except BaseException:
        upload.unlink(missing_ok=True)
        raise
    return upload


def _conditions(request: Request) -> dict:
    return {"mtime": _mtime(request.headers.get("x-mtime")), "if_match": request.headers.get("if-match"),
            "if_none_match": request.headers.get("if-none-match")}


async def _in_thread(settings: Settings, work):
    """Run work(conn) off the event loop with its own connection; SyncError becomes the HTTP refusal."""
    def run():
        conn = db.connect(settings.db_path)
        try:
            return work(conn)
        finally:
            conn.close()
    try:
        return await anyio.to_thread.run_sync(run)
    except sync.SyncError as exc:
        raise _refuse(exc) from None


def _written(result: dict) -> JSONResponse:
    return JSONResponse(result, headers={"ETag": f'"{result["version"]}"'})


@router.put("/api/sync/roots/{root_id}/files")
async def put_file(root_id: int, request: Request, path: str = Query(..., max_length=sync.MAX_PATH),
                   caller: Caller = Depends(sync_caller), settings: Settings = Depends(get_settings)):
    """One whole file (at most 32 MB) at `path` under the root. Headers: X-Sha256 (required), X-Mtime,
    If-None-Match: * for a new file or If-Match: <version> to replace one. Answers the new version (ETag)."""
    claimed = _claimed_sha(request)
    conditions = _conditions(request)
    upload = await _receive(request, settings, sync.MAX_PUT_BYTES, claimed)

    def write(conn):
        try:
            return sync.write_file(conn, settings, _root(conn, caller, root_id), path, upload, claimed,
                                   user_id=caller.id, token_id=caller.token_id, **conditions)
        finally:
            upload.unlink(missing_ok=True)

    return _written(await _in_thread(settings, write))


@router.get("/api/sync/roots/{root_id}/files")
def get_file(root_id: int, path: str = Query(..., max_length=sync.MAX_PATH), version: int | None = Query(None),
             caller: Caller = Depends(sync_caller), conn: sqlite3.Connection = Depends(get_db),
             settings: Settings = Depends(get_settings)):
    """A backed-up file (or one of its earlier versions), for Restore. Range requests work."""
    from mnemerynne import editing, share

    root = _root(conn, caller, root_id)
    try:
        row = sync.file_at(conn, root, path)
    except sync.SyncError as exc:
        raise _refuse(exc) from None
    if version is not None:
        v = conn.execute("SELECT * FROM file_versions WHERE id = ? AND file_id = ?", (version, row["id"])).fetchone()
        if v is None or not editing.version_path(settings, v["rel_path"]).is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That version isn't kept any more.")
        return FileResponse(editing.version_path(settings, v["rel_path"]), media_type="application/octet-stream")
    try:
        disk = share.file_path(conn, row["id"])
    except share.ShareError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    headers = {"X-Mtime": f"{row['disk_mtime_ns'] / 1e9:.6f}"} if row["disk_mtime_ns"] else {}
    return FileResponse(disk, media_type="application/octet-stream", headers=headers)


@router.get("/api/sync/roots/{root_id}/versions")
def file_versions(root_id: int, path: str = Query(..., max_length=sync.MAX_PATH),
                  caller: Caller = Depends(sync_caller), conn: sqlite3.Connection = Depends(get_db)):
    try:
        return {"versions": sync.versions(conn, _root(conn, caller, root_id), path)}
    except sync.SyncError as exc:
        raise _refuse(exc) from None


# ------------------------------------------------------- uploads in parts
class UploadBody(BaseModel):
    root_id: int
    path: str = Field(..., max_length=sync.MAX_PATH)
    size: int = Field(..., gt=0)
    sha256: str = Field(..., min_length=64, max_length=64)


@router.post("/api/sync/uploads")
def start_upload(body: UploadBody, caller: Caller = Depends(sync_caller), conn: sqlite3.Connection = Depends(get_db),
                 settings: Settings = Depends(get_settings)):
    """Start (or resume) a big file's upload; answers the part size and the parts already here."""
    try:
        return sync.start_upload(conn, settings, _root(conn, caller, body.root_id), body.path, body.size, body.sha256)
    except sync.SyncError as exc:
        raise _refuse(exc) from None


@router.get("/api/sync/uploads/{upload_id}")
def upload_status(upload_id: str, caller: Caller = Depends(sync_caller), conn: sqlite3.Connection = Depends(get_db),
                  settings: Settings = Depends(get_settings)):
    try:
        return sync.upload_status(conn, settings, caller.id, upload_id)
    except sync.SyncError as exc:
        raise _refuse(exc) from None


@router.put("/api/sync/uploads/{upload_id}/parts/{n}")
async def put_part(upload_id: str, n: int, request: Request, caller: Caller = Depends(sync_caller),
                   settings: Settings = Depends(get_settings)):
    claimed = _claimed_sha(request)
    data = await _receive(request, settings, sync.PART_BYTES, claimed)

    def store(conn):
        try:
            return sync.store_part(conn, settings, caller.id, upload_id, n, data, claimed)
        finally:
            data.unlink(missing_ok=True)

    return await _in_thread(settings, store)


@router.post("/api/sync/uploads/{upload_id}/finish")
async def finish_upload(upload_id: str, request: Request, caller: Caller = Depends(sync_caller),
                        settings: Settings = Depends(get_settings)):
    """Every part is here: the file goes in place, as with a PUT (same headers, same answer)."""
    conditions = _conditions(request)

    def finish(conn):
        return sync.finish_upload(conn, settings, lambda root_id: _root(conn, caller, root_id), caller.id, upload_id,
                                  token_id=caller.token_id, **conditions)

    return _written(await _in_thread(settings, finish))


@router.delete("/api/sync/uploads/{upload_id}")
def drop_upload(upload_id: str, caller: Caller = Depends(sync_caller), conn: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    try:
        sync.drop_upload(conn, settings, caller.id, upload_id)
    except sync.SyncError as exc:
        raise _refuse(exc) from None
    return {"status": "dropped"}


class PathsBody(BaseModel):
    paths: list[str] = Field(..., min_length=1, max_length=1000)


@router.post("/api/sync/roots/{root_id}/delete")
def delete_paths(root_id: int, body: PathsBody, caller: Caller = Depends(sync_caller),
                 conn: sqlite3.Connection = Depends(get_db)):
    """Move paths (files or folders) to the recycle bin; already gone counts as done."""
    root = _root(conn, caller, root_id)
    results = sync.delete(conn, root, body.paths, caller.id)
    done = sum(1 for r in results if r["ok"] and not r.get("gone"))
    if done:
        _log(conn, caller.user, "backup.delete", caller.user["username"],
             f"{root.computer}\\{root.label}: {done} to the recycle bin")
    return {"results": results}


class Move(BaseModel):
    src: str = Field(..., alias="from", max_length=sync.MAX_PATH)
    dest: str = Field(..., alias="to", max_length=sync.MAX_PATH)


class MovesBody(BaseModel):
    moves: list[Move] = Field(..., min_length=1, max_length=1000)


@router.post("/api/sync/roots/{root_id}/move")
def move_paths(root_id: int, body: MovesBody, caller: Caller = Depends(sync_caller),
               conn: sqlite3.Connection = Depends(get_db)):
    """Renames and moves within the root, so they aren't uploaded again."""
    root = _root(conn, caller, root_id)
    return {"results": sync.move(conn, root, [(m.src, m.dest) for m in body.moves], caller.id)}


# --------------------------------------------------- Preferences → Backups
def _backup_user(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    if not sync.may_backup(conn, user, request.app.state.directory):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Backups aren't open for you; ask an admin.")
    return user


@router.get("/api/me/backups")
def my_backups(user=Depends(_backup_user), conn: sqlite3.Connection = Depends(get_db),
               settings: Settings = Depends(get_settings)):
    """The person's backed-up folders, the computers signed in, and the app to download."""
    builds = sync.client_downloads(settings)
    signins = [{"kind": "token", "id": t["id"], "name": t["name"], "created_at": t["created_at"],
                "last_used_at": t["last_used_at"], "expires_at": t["expires_at"]}
               for t in api_tokens.list_for_user(conn, user["id"], scope="sync") if t["active"]]
    signins += [{"kind": "oauth", "id": c["id"], "name": c["name"], "created_at": c["created_at"],
                 "last_used_at": c["last_used_at"], "expires_at": None}
                for c in oauth.connections(conn, user["id"], "sync")]
    return {"roots": sync.roots(conn, user["id"]), "signins": signins, "latest": builds["latest"],
            "downloads": {kind: f"/downloads/backup/{name}" for kind, name in builds["files"].items()}}


@router.patch("/api/me/backups/roots/{root_id}")
def change_my_root(root_id: int, body: RootChange, user=Depends(_backup_user),
                   conn: sqlite3.Connection = Depends(get_db)):
    """Searchable on or off (or a new label) from Preferences."""
    return _change_root(conn, user, root_id, body)


@router.delete("/api/me/backups/signins/{kind}/{signin_id}")
def sign_out_computer(kind: str, signin_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Sign one computer's backup app out (a pasted token, or a browser sign-in)."""
    if kind == "token":
        ok = any(t["id"] == signin_id for t in api_tokens.list_for_user(conn, user["id"], scope="sync")) and \
            api_tokens.revoke(conn, signin_id, user["id"])
    elif kind == "oauth":
        ok = any(c["id"] == signin_id for c in oauth.connections(conn, user["id"], "sync")) and \
            oauth.revoke_grant(conn, signin_id, user["id"])
    else:
        ok = False
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such sign-in.")
    return {"status": "signed out"}


@router.get("/downloads/backup/{name}")
def download_client(name: str, request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db),
                    settings: Settings = Depends(get_settings)):
    """The installer or AppImage (data_dir/backup-client), for people who may back up."""
    if not sync.may_backup(conn, user, request.app.state.directory):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Backups aren't open for you; ask an admin.")
    path = sync.client_file(settings, name)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such download.")
    return FileResponse(path, media_type="application/octet-stream", filename=name)


# -------------------------------------------------------------------- admin
def _status(conn: sqlite3.Connection, settings: Settings) -> dict:
    cfg = sync.load_settings(conn)
    return {"settings": cfg | {"group": group_refs.label(conn, cfg["group"])}, "backup_url": settings.backup_url,
            "roots": sync.all_roots(conn)}


class BackupsSettingsBody(BaseModel):
    enabled: bool | None = None
    group: str | None = Field(None, max_length=1000)     # who may back up: AD or Entra group(s), separated by ;


@admin_router.get("/api/admin/settings/backups")
def admin_backups(user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db),
                  settings: Settings = Depends(get_settings)):
    return _status(conn, settings)


@admin_router.put("/api/admin/settings/backups")
def admin_save_backups(body: BackupsSettingsBody, request: Request, user=Depends(admin_user),
                       conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Backups on or off, and who may use them; only the fields sent change. Logged."""
    changes = body.model_dump(exclude_none=True)
    if "group" in changes:
        directory = request.app.state.directory
        try:
            changes["group"] = group_refs.resolve(conn, changes["group"], directory.groups() if directory.enabled else [])
        except group_refs.GroupRefError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    if changes:
        with transaction(conn):
            sync.save_settings(conn, changes)
            _log(conn, user, "settings.backups", ", ".join(f"{k}={v}" for k, v in changes.items()))
    return _status(conn, settings)
