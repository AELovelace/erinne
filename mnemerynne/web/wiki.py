"""The Wiki tab: the organisation's wiki library (mnemerynne/wiki.py) as a page tree with each page
rendered, an Edit button into the editor, and the admin setting that picks the library."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from mnemerynne import fs, share, wiki
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.ingest import markdown, store
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import admin_user, api_user, get_db, get_settings, is_admin, page_user
from mnemerynne.web.files import _readable_file

router = APIRouter()


def _readable_library(conn: sqlite3.Connection, access: fs.Access) -> sqlite3.Row | None:
    row = wiki.library_row(conn)
    return row if row is not None and row["id"] in access.readable else None


def _wiki_page(request: Request, conn: sqlite3.Connection, user, file_id: int | None):
    access = fs.Access.of(conn, user["id"])
    library = _readable_library(conn, access)
    return request.app.state.templates.TemplateResponse(request, "wiki.html", {
        "user": user, "is_admin": is_admin(request, conn, user), "active": "wiki",
        "library": {"id": library["id"], "name": library["name"]} if library else None,
        "default_name": wiki.DEFAULT_NAME, "file_id": file_id,
        "can_write": bool(library) and access.can_write(library["id"], None)})


@router.get("/wiki", response_class=HTMLResponse)
def wiki_home(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
    return _wiki_page(request, conn, user, None)


@router.get("/wiki/{file_id}", response_class=HTMLResponse)
def wiki_page(request: Request, file_id: int, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
    return _wiki_page(request, conn, user, file_id)


@router.get("/api/wiki")
def wiki_tree(user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """The wiki library and its pages (what this person may read), or library null when there is none."""
    access = fs.Access.of(conn, user["id"])
    library = _readable_library(conn, access)
    if library is None:
        return {"library": None}
    return {"library": {"id": library["id"], "name": library["name"]}, **wiki.tree(conn, access, library["id"])}


@router.get("/api/wiki/pages/{file_id}")
def wiki_page_html(file_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db),
                   settings: Settings = Depends(get_settings)):
    """One page rendered, with links to other pages resolved; read straight from the file, so a page
    saved a moment ago is already current."""
    access = fs.Access.of(conn, user["id"])
    library = _readable_library(conn, access)
    row = _readable_file(conn, access, file_id)
    if library is None or row["collection_id"] != library["id"] or row["deleted_at"] is not None \
            or not wiki.is_page(row["display_name"]) or not fs.is_live_folder(conn, row["folder_id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such wiki page")
    try:
        text = markdown.decode(store.content_path(conn, settings, file_id).read_bytes())
    except share.ShareUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except OSError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File content is missing") from None
    except markdown.MarkdownError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from None
    can_write = access.can_write(row["collection_id"], row["folder_id"])
    return {"id": file_id, "name": row["display_name"], "title": wiki.title_of(row["display_name"]),
            "folder_id": row["folder_id"], "path": [p["name"] for p in fs.folder_path(conn, row["folder_id"])],
            "html": markdown.render_html(text, link_rewriter=wiki.rewriter(conn, access, library["id"], row)),
            "can_write": can_write, "edit_url": f"/edit/{file_id}?back=/wiki/{file_id}" if can_write else None,
            "view_url": f"/files/{file_id}/view", "updated_at": row["updated_at"], "edited_via": row["edited_via"]}


# ------------------------------------------------------------ admin setting
def _status(conn: sqlite3.Connection) -> dict:
    chosen = wiki.setting(conn)
    library = wiki.library_row(conn)
    return {"library_id": library["id"] if library else None, "name": library["name"] if library else None,
            "source": "setting" if library and chosen == library["id"] else ("name" if library else "none"),
            "default_name": wiki.DEFAULT_NAME,
            "libraries": [{"id": r["id"], "name": r["name"]} for r in conn.execute(
                "SELECT id, name FROM collections WHERE kind = 'library' ORDER BY name COLLATE NOCASE")]}


class WikiLibraryBody(BaseModel):
    library_id: int | None = None   # None: the library called Wiki


@router.get("/api/admin/settings/wiki-library")
def get_wiki_library(user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return _status(conn)


@router.put("/api/admin/settings/wiki-library")
def set_wiki_library(body: WikiLibraryBody, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    old = _status(conn)
    try:
        with transaction(conn):
            wiki.set_library(conn, body.library_id)
            new = _status(conn)
            _log(conn, user, "settings.wiki_library", new["name"] or f"(the library called {wiki.DEFAULT_NAME})",
                 f"was {old['name'] or '(none)'}")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return new
