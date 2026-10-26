"""Editing text and Markdown files in the browser: the editor page (/edit/{id},
/edit/new) and the endpoints behind it (mdeditor.js).

Saves go through editing.replace_content, the same path as Claude's edits, so
every change keeps the previous version (Files → Versions), a file someone else
saved in the meantime is refused (409: the editor shows both ways out), a file
open in Word or Excel is refused, and on the share the new content lands in one
rename. Permission is the person's own (fs.Access: what they may change in
Files), not the AI rules. The live preview is rendered here with the same
renderer as the file view, so what the editor shows is what Files will show.
"""

from __future__ import annotations

import io
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from mnemerynne import editing, fs, permissions, proposals, share
from mnemerynne.config import Settings
from mnemerynne.ingest import markdown, store
from mnemerynne.web import security
from mnemerynne.web.deps import api_user, get_db, get_settings, is_admin, page_user
from mnemerynne.web.files import _file_json, _fs_errors, _readable_file, _writable_file

router = APIRouter()

MAX_CONTENT_CHARS = 20 * 1024 * 1024   # the byte limit (edit_max_bytes) is checked on save
PREVIEW_MAX_CHARS = 2_000_000
PREVIEW_PER_MINUTE = 120               # per person; the editor asks at most every 400 ms while typing
_preview_limiter = security.RateLimiter()


def _text_file(conn: sqlite3.Connection, settings: Settings, access: fs.Access, file_id: int, *,
               write: bool) -> tuple[sqlite3.Row, str, str]:
    """A live .txt or .md file the person may read (or write): its row, text and version."""
    row = _writable_file(conn, access, file_id) if write else _readable_file(conn, access, file_id)
    if row["deleted_at"] is not None or editing.format_of(row["display_name"]) != "text" \
            or not fs.is_live_folder(conn, row["folder_id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a text or Markdown file")
    try:
        path = store.content_path(conn, settings, file_id)
        if path.stat().st_size > settings.edit_max_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                                f"“{row['display_name']}” is over {settings.edit_max_bytes // (1024 * 1024)} MB, "
                                f"too big to edit here.")
        data = path.read_bytes()
    except share.ShareUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except OSError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File content is missing") from None
    try:
        text = markdown.decode(data)
    except markdown.MarkdownError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from None
    return row, text, editing.sha256(data)


def _out(conn: sqlite3.Connection, access: fs.Access, file_id: int, version: str, text: str | None = None) -> dict:
    row = fs.file_row(conn, file_id)
    out = {**_file_json(row, access), "version": version,
           "can_write": access.can_write(row["collection_id"], row["folder_id"]) and row["deleted_at"] is None}
    if text is not None:
        out["content"] = text
    return out


# ------------------------------------------------------------------- content
@router.get("/api/files/{file_id}/text")
def read_text(file_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db),
              settings: Settings = Depends(get_settings)):
    """The file's text and the version it is (pass it back when saving)."""
    access = fs.Access.of(conn, user["id"])
    _, text, version = _text_file(conn, settings, access, file_id, write=False)
    return _out(conn, access, file_id, version, text)


class TextBody(BaseModel):
    content: str = Field(max_length=MAX_CONTENT_CHARS)
    version: str | None = Field(None, max_length=64)   # the version the text was loaded from


@router.put("/api/files/{file_id}/text")
def save_text(file_id: int, body: TextBody, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db),
              settings: Settings = Depends(get_settings)):
    """Save the whole text. 409 when the file changed since `version` or is open in Office."""
    access = fs.Access.of(conn, user["id"])
    _text_file(conn, settings, access, file_id, write=True)
    try:
        version = editing.replace_content(conn, settings, file_id, body.content.encode("utf-8"),
                                          expected=body.version, actor_id=user["id"], via="web",
                                          summary="edited in the browser")
    except editing.EditError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except share.ShareError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return _out(conn, access, file_id, version)


class NewTextBody(BaseModel):
    collection_id: int | None = None   # default: the person's File box
    folder_id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    content: str = Field("", max_length=MAX_CONTENT_CHARS)


@router.post("/api/files/text")
def create_text(body: NewTextBody, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    """A new text or Markdown page (".md" is added when the name has no text extension) where the
    person may write; the same checks as an upload. An empty page starts with its name as a heading."""
    access = fs.Access.of(conn, user["id"])
    target = body.collection_id if body.collection_id is not None else permissions.private_collection_id(conn, user["id"])
    if target is None or target not in access.writable:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't create files here")
    _fs_errors(lambda: fs.require_folder(conn, target, body.folder_id))
    if not access.can_write(target, body.folder_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't create files in this folder")
    name = store.clean_display_name(body.name)
    if editing.format_of(name) != "text":
        name += ".md"
    data = body.content.encode("utf-8")
    if not data.strip():
        data = f"# {name.rsplit('.', 1)[0]}\n".encode("utf-8")
    try:
        received = store.receive(io.BytesIO(data), name, settings)
    except store.UploadError as exc:
        code = status.HTTP_413_CONTENT_TOO_LARGE if isinstance(exc, store.TooLarge) else status.HTTP_400_BAD_REQUEST
        raise HTTPException(code, str(exc)) from exc
    file_id, created = _fs_errors(lambda: store.add_file(conn, settings, received, target, user["id"],
                                                         folder_id=body.folder_id))
    return JSONResponse(_out(conn, access, file_id, editing.sha256(data)),
                        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PreviewBody(BaseModel):
    text: str = Field("", max_length=PREVIEW_MAX_CHARS)


@router.post("/api/markdown/preview")
def preview(body: PreviewBody, user=Depends(api_user)):
    """The editor's live preview: the same rendering as the file view (raw HTML escaped, links checked)."""
    if not _preview_limiter.allow(f"preview:{user['id']}", PREVIEW_PER_MINUTE, 60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "The preview is refreshing too often; it catches up in a moment.")
    return {"html": markdown.render_html(body.text)}


# --------------------------------------------------------------------- pages
def _page(request: Request, conn: sqlite3.Connection, user, **extra):
    return request.app.state.templates.TemplateResponse(request, "editor.html", {
        "user": user, "is_admin": is_admin(request, conn, user), **extra})


@router.get("/edit/new", response_class=HTMLResponse)
def new_page(request: Request, collection_id: int | None = None, folder_id: int | None = None,
             name: str = "", back: str = "/files", user=Depends(page_user),
             conn: sqlite3.Connection = Depends(get_db)):
    """The editor for a page that doesn't exist yet, in a place the person may write to."""
    access = fs.Access.of(conn, user["id"])
    target = collection_id if collection_id is not None else permissions.private_collection_id(conn, user["id"])
    if target is None or target not in access.writable:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't create files here")
    _fs_errors(lambda: fs.require_folder(conn, target, folder_id))
    if not access.can_write(target, folder_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't create files in this folder")
    return _page(request, conn, user, mode="new", file_id=None, collection_id=target, folder_id=folder_id,
                 name=name[:200], where=proposals.where(conn, target, folder_id), view_url=None,
                 back=back if back.startswith("/") and not back.startswith("//") else "/files")


@router.get("/edit/{file_id}", response_class=HTMLResponse)
def edit_page(request: Request, file_id: int, back: str = "/files", user=Depends(page_user),
              conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """The editor for a text or Markdown file the person may change."""
    access = fs.Access.of(conn, user["id"])
    row, _, _ = _text_file(conn, settings, access, file_id, write=True)
    return _page(request, conn, user, mode="edit", file_id=file_id, collection_id=row["collection_id"],
                 folder_id=row["folder_id"], name=row["display_name"],
                 where=proposals.where(conn, row["collection_id"], row["folder_id"]),
                 view_url=f"/files/{file_id}/view",
                 back=back if back.startswith("/") and not back.startswith("//") else "/files")
