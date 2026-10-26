"""The Remote tab: the LAN apps this person may open (mnemerynne/remote.py), shown in a window with
a home page and a resource bar (remote.js); the apps themselves are served by RemoteProxy under
/remote/<slug>/."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from mnemerynne import remote
from mnemerynne.web.deps import api_user, get_db, is_admin, page_user

router = APIRouter()


def _apps(request: Request, conn: sqlite3.Connection, user) -> list[dict]:
    return [remote.app_json(r) for r in remote.apps_for(conn, user, request.app.state.directory,
                                                         is_admin(request, conn, user))]


@router.get("/remote", response_class=HTMLResponse)
def remote_page(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
    return request.app.state.templates.TemplateResponse(request, "remote.html", {
        "user": user, "is_admin": is_admin(request, conn, user), "active": "remote",
        "apps": _apps(request, conn, user)})


@router.get("/api/remote")
def remote_apps(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    return {"apps": _apps(request, conn, user)}
