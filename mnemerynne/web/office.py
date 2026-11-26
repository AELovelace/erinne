"""The ONLYOFFICE editor in the browser (mnemerynne/office.py has the design).

People:      /office/{id} (the page), /api/office/{id}/config (its signed editor config), /api/office/ds-auth (nginx's
             auth_request for the edge's /onlyoffice/: only signed-in people reach the Document Server).
Container:   /api/office/ds/file/{id} (the file's bytes) and /api/office/ds/callback/{id} (saves). Both need our URL
             token from the config, the Document Server's JWT and a request from onlyoffice_allowed_ips; the edge
             lets /api/office/ds/ in only from podman's network as well.
"""

from __future__ import annotations

import logging
import sqlite3

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse

from mnemerynne import db, fs, group_refs, office, share
from mnemerynne.config import Settings
from mnemerynne.ingest import store
from mnemerynne.web.deps import api_user, get_db, get_settings, is_admin, page_user
from mnemerynne.web.files import _readable_file
from mnemerynne.web.sendfile import send_file

log = logging.getLogger(__name__)
router = APIRouter()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None                    # uvicorn already applied X-Forwarded-For


def _deny(exc: office.OfficeError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _open_file(request: Request, conn: sqlite3.Connection, settings: Settings, user, file_id: int):
    """The file, the person's mode on it ('edit'/'view') and its size, or the reason they can't open it here."""
    if not office.allowed(conn, settings, user, request.app.state.directory):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The Office editor isn't on for you.")
    access = fs.Access.of(conn, user["id"])
    row = _readable_file(conn, access, file_id)                                # same 404 for missing and not yours
    if row["deleted_at"] is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    mode = office.mode_for(conn, access, row)
    if mode is None:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            f"“{row['display_name']}” isn't a Word, Excel or PowerPoint file.")
    if (row["size"] or 0) > settings.onlyoffice_max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                            f"“{row['display_name']}” is over {settings.onlyoffice_max_bytes // (1024 * 1024)} MB, "
                            f"too big for the Office editor. Download it instead.")
    return row, mode


def _sha(conn: sqlite3.Connection, file_id: int) -> str:
    """The file's current version (its content's SHA-256; fs.file_row doesn't carry it)."""
    return conn.execute("SELECT blob_sha256 FROM files WHERE id = ?", (file_id,)).fetchone()[0]


def _back(row: sqlite3.Row) -> str:
    """Files, opened at the file's folder."""
    where = f"/files#c={row['collection_id']}"
    return where + (f"&f={row['folder_id']}" if row["folder_id"] else "")


@router.get("/office/{file_id}", response_class=HTMLResponse)
def office_page(request: Request, file_id: int, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    """The editor page: the flower bar on top, the Document Server's editor filling the rest."""
    row, mode = _open_file(request, conn, settings, user, file_id)
    return request.app.state.templates.TemplateResponse(request, "office.html", {
        "user": user, "is_admin": is_admin(request, conn, user), "active": "files",
        "file_id": file_id, "name": row["display_name"], "mode": mode, "api_js": office.api_js(settings),
        "back": _back(row)})


@router.get("/api/office/{file_id}/config")
def office_config(request: Request, file_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db),
                  settings: Settings = Depends(get_settings)):
    """The signed DocsAPI config. Editors join (or start) the file's session, so everyone shares one key; viewers get
    a key of the current version and never lock anything."""
    row, mode = _open_file(request, conn, settings, user, file_id)
    sha = _sha(conn, file_id)
    if mode == "edit":
        sess = office.open_session(conn, file_id, sha, user["id"])
    else:
        live = office.session(conn, file_id)
        key = live.doc_key if live else f"{file_id}-{sha[:20]}-v"              # stable per version: cached nicely
        sess = office.Session(file_id, key, sha, [])
    lang = (request.headers.get("accept-language") or "en")[:2].lower() or "en"
    back = str(request.base_url).rstrip("/") + _back(row)                     # the editor's "Go to folder" link
    return office.editor_config(settings, row, user, mode, sess, lang=lang, back_url=back,
                                fallback_base=str(request.base_url))         # absolute URLs even without PUBLIC_URL


@router.get("/api/office/ds-auth")
def office_ds_auth(user=Depends(api_user)):
    """nginx auth_request for /onlyoffice/: 204 for a signed-in person (api_user answers 401 otherwise)."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ the Document Server's side
def _container(request: Request, settings: Settings) -> None:
    """The request comes from the Document Server: configured, from its network, signed with the shared secret."""
    if not office.configured(settings):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not configured")
    if not office.ip_allowed(settings, _client_ip(request)):
        log.warning("onlyoffice: %s refused from %s", request.url.path, _client_ip(request))
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not from the Document Server")


@router.get("/api/office/ds/file/{file_id}")
def office_ds_file(request: Request, file_id: int, t: str = "", conn: sqlite3.Connection = Depends(get_db),
                   settings: Settings = Depends(get_settings)):
    """The file's bytes for the Document Server (opening it in the editor)."""
    _container(request, settings)
    try:
        token = office.bearer(request.headers.get("authorization"))
        if token is None:
            raise office.OfficeForbidden("Unsigned request.")
        office.jwt_decode(token, settings.onlyoffice_jwt_secret)               # signed with the shared secret
        key = office.check_url_token(settings, t, file_id, "dl", office.TOKEN_AGE)
    except office.OfficeError as exc:
        raise _deny(exc) from None
    row = fs.file_row(conn, file_id)
    if row is None or row["deleted_at"] is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    live = office.session(conn, file_id)
    viewer_key = f"{file_id}-{_sha(conn, file_id)[:20]}-v"
    if key != viewer_key and (live is None or live.doc_key != key):            # an old key: the file moved on
        raise HTTPException(status.HTTP_410_GONE, "That version isn't current any more.")
    try:
        path = store.content_path(conn, settings, file_id)
    except share.ShareUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    return send_file(path, media_type="application/octet-stream", filename=row["display_name"],
                     content_disposition_type="attachment", headers={"X-Content-Type-Options": "nosniff"})


@router.post("/api/office/ds/callback/{file_id}")
async def office_ds_callback(request: Request, file_id: int, t: str = "",
                             settings: Settings = Depends(get_settings)):
    """The Document Server reporting on an editing session (see office.py). Always answers {"error": 0} once the
    outcome is recorded, {"error": 1} only when trying again later could help (it retries)."""
    _container(request, settings)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not an object")
    try:
        cb = office.verify_callback(settings, body, request.headers.get("authorization"))   # unsigned → 403
        key = office.check_url_token(settings, t, file_id, "cb", office.CALLBACK_AGE)
        if cb.key != key:                                                     # the token was made for another session
            raise office.OfficeForbidden("The callback's key doesn't match its link.")
    except office.OfficeError as exc:
        raise _deny(exc) from None
    directory = request.app.state.directory

    def work() -> dict:
        conn = db.connect(settings.db_path)                                   # own connection: this can take a while
        try:
            if cb.status in (1, 4):
                outcome = office.note_status(conn, file_id, cb)
            elif cb.status in (2, 6):
                src, sha = office.download(settings, office.fetch_url(settings, cb.url))

                def may_edit(user: sqlite3.Row, row: sqlite3.Row) -> bool:     # their rights now, not at opening
                    access = fs.Access.of(conn, user["id"])
                    if not (access.can_read_file(row) and access.can_write_file(row)
                            and fs.is_live_folder(conn, row["folder_id"])):
                        return False
                    value = office.group(conn)
                    return not value or group_refs.in_any(conn, user, directory, value)

                outcome = office.save_edit(conn, settings, file_id, cb, src, sha, may_edit=may_edit)
            else:                                                             # 3 / 7: the Document Server failed
                log.error("onlyoffice: file %s session %s reported save error (status %s)",
                          file_id, cb.key, cb.status)
                outcome = office.Outcome("ignored", f"status {cb.status}")
            log.info("onlyoffice: file %s status %s → %s %s", file_id, cb.status, outcome.result, outcome.detail)
            return {"error": 0}
        finally:
            conn.close()

    try:
        return JSONResponse(await anyio.to_thread.run_sync(work))
    except office.OfficeForbidden as exc:
        raise _deny(exc) from None
    except Exception:                                                         # share down, fetch failed: retry later
        log.exception("onlyoffice: callback for file %s failed; the Document Server will retry", file_id)
        return JSONResponse({"error": 1})
