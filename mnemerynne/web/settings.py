"""Personal settings: everyone's own account page. Pick a theme, change your
password, sign out other browsers, and link Microsoft 365 accounts (m365.js,
web/m365.py)."""

from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from mnemerynne import accounts, api_tokens, auth, users
from mnemerynne.db import transaction
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import api_user, get_db, is_admin, page_user

router = APIRouter()

ADMIN_VIA = {"role": "local admin", "bootstrap": "admin (server setting)", "domain": "admin (domain group)"}


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
    state = request.app.state
    via = accounts.admin_via(conn, user, state.directory, state.settings.admin_usernames)
    groups = [r[0] for r in conn.execute(
        "SELECT g.name FROM group_members m JOIN groups g ON g.id = m.group_id WHERE m.user_id = ? "
        "ORDER BY g.name COLLATE NOCASE", (user["id"],))]
    return state.templates.TemplateResponse(request, "settings.html", {
        "user": user, "is_admin": is_admin(request, conn, user), "active": "settings",
        "admin_via": ADMIN_VIA.get(via or ""), "groups": groups, "domain": state.settings.ad_domain,
        "tokens": api_tokens.has_active(conn, user["id"]), "min_password": auth.MIN_PASSWORD_LENGTH})


def _keep_this_session(request: Request, conn: sqlite3.Connection, user_id: int) -> None:
    """After bumping session_epoch: other browsers are signed out, this one stays."""
    request.session["epoch"] = users.get_user_by_id(conn, user_id)["session_epoch"]


class PasswordChange(BaseModel):
    current: str = Field(min_length=1, max_length=1024)
    new: str = Field(min_length=1, max_length=1024)


@router.post("/api/me/password")
def change_password(body: PasswordChange, request: Request, user=Depends(api_user),
                    conn: sqlite3.Connection = Depends(get_db)):
    """Change your own password. It needs the current one (failures count
    towards the sign-in lockout); other browsers are signed out."""
    throttle = request.app.state.throttle
    client_ip = request.client.host if request.client else ""
    if throttle.locked_out(user["username"], client_ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many wrong passwords. Try again in a few minutes.")
    if users.authenticate(conn, user["username"], body.current) is None:
        throttle.record_failure(user["username"], client_ip)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Your current password isn't right.")
    if body.new == body.current:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick a password different from the current one.")
    try:
        with transaction(conn):
            users.set_password(conn, user["username"], body.new)
            _log(conn, user, "account.password", user["username"], "changed it themselves")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    throttle.clear(user["username"], client_ip)
    _keep_this_session(request, conn, user["id"])
    return {"status": "changed"}


THEME_COOKIE = "erynne_theme"   # so the sign-in page matches, read in base.html


class ThemeBody(BaseModel):
    theme: Literal["sakura", "neon"]


@router.put("/api/me/theme")
def set_theme(body: ThemeBody, response: Response, user=Depends(api_user),
              conn: sqlite3.Connection = Depends(get_db)):
    conn.execute("UPDATE users SET theme = ? WHERE id = ?", (body.theme, user["id"]))
    response.set_cookie(THEME_COOKIE, body.theme, max_age=365 * 86400, samesite="lax", httponly=True)
    return {"theme": body.theme}


@router.post("/api/me/sign-out-others")
def sign_out_others(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """End every other browser's session (Claude tokens are separate: see the Claude page)."""
    conn.execute("UPDATE users SET session_epoch = session_epoch + 1 WHERE id = ?", (user["id"],))
    _keep_this_session(request, conn, user["id"])
    return {"status": "signed out"}
