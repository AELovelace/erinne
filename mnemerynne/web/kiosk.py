"""Kiosk settings for a screen box: the TV's code, the admin's approval on a phone, and the box's helper
redeeming it for a session to change that screen's settings (mnemerynne/media/kiosk.py).

The TV (signed in as its screen) starts and polls an unlock; an admin approves at /media/kiosk; the
helper (deploy/screen/wifi-setup.py, no cookies) redeems the grant and sends the session token in the
X-Kiosk-Session header after that.
"""

from __future__ import annotations

import sqlite3

import segno
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from mnemerynne.db import transaction
from mnemerynne.media import kiosk, screens
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import _session_user, admin_user, get_db, is_admin
from mnemerynne.web.media import _media_on, screen_device

router = APIRouter(dependencies=[Depends(_media_on)])


def unlocks(request: Request) -> kiosk.Unlocks:
    if getattr(request.app.state, "media_kiosk", None) is None:
        request.app.state.media_kiosk = kiosk.Unlocks()
    return request.app.state.media_kiosk


def _approve_url(request: Request, code: str) -> str:
    base = request.app.state.settings.public_url.rstrip("/") or str(request.base_url).rstrip("/")
    return f"{base}/media/kiosk?code={code}"


# -------------------------------------------------------------------- the TV
@router.post("/media/screen/kiosk/unlock")
def start_unlock(request: Request, screen=Depends(screen_device)):
    """A code for an admin to approve, with a QR code of where to approve it."""
    try:
        code, secret = unlocks(request).start(screen["id"])
    except screens.ScreenError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    url = _approve_url(request, code)
    return {"code": screens.show_code(code), "secret": secret, "url": url, "expires_in": kiosk.CODE_SECONDS,
            "qr": segno.make(url, error="m").svg_inline(scale=1, border=4, omitsize=True)}


@router.get("/media/screen/kiosk/unlock")
def poll_unlock(request: Request, secret: str = "", screen=Depends(screen_device)):
    """Still waiting, approved (with the grant the box's helper redeems, once), or expired."""
    return unlocks(request).poll(screen["id"], secret)


# ---------------------------------------------------------- the admin's phone
@router.get("/media/kiosk", response_class=HTMLResponse)
def approve_page(request: Request, code: str = "", conn: sqlite3.Connection = Depends(get_db)):
    """The TV's QR code: approve opening that screen's Kiosk settings. Signed out, sign in first."""
    user = _session_user(request, conn)
    if user is None:
        request.session["after_login"] = str(request.url.path) + ("?" + str(request.url.query) if request.url.query else "")
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return request.app.state.templates.TemplateResponse(request, "kiosk_approve.html", {
        "user": user, "is_admin": is_admin(request, conn, user), "active": "media",
        "code": screens.show_code(screens.clean_code(code)) if code else ""})


class ApproveBody(BaseModel):
    code: str = Field(..., max_length=40)


@router.post("/api/media/kiosk/approve")
def approve(body: ApproveBody, request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """An admin opens Kiosk settings on the TV showing this code."""
    u = unlocks(request)
    try:
        code, screen_id = u.check(user["id"], body.code)
    except screens.ScreenError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    row = conn.execute("SELECT name FROM media_screens WHERE id = ?", (screen_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That screen was removed.")
    u.approve(code, user["id"], user["username"])
    with transaction(conn):
        _log(conn, user, "kiosk.unlock", row["name"])
    return {"screen": row["name"]}


# ----------------------------------------------------------- the box's helper
class RedeemBody(BaseModel):
    grant: str = Field(..., max_length=200)


def _screen_row(conn: sqlite3.Connection, screen_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM media_screens WHERE id = ?", (screen_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That screen was removed.")
    return row


@router.post("/api/media/kiosk/redeem")
def redeem(body: RedeemBody, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """The helper hands in the TV's grant (once) and gets a session for that screen's settings."""
    try:
        token, info = unlocks(request).redeem(body.grant)
    except screens.ScreenError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    return {"session": token, "admin": info["admin"]["name"], "expires_in": info["expires_in"],
            "screen": kiosk.settings_of(_screen_row(conn, info["screen"]))}


def kiosk_session(request: Request, x_kiosk_session: str = Header("", max_length=200)) -> dict:
    found = unlocks(request).session(x_kiosk_session)
    if found is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "The settings were locked again. Approve from a phone.")
    return found


@router.get("/api/media/kiosk/screen")
def screen_settings(session=Depends(kiosk_session), conn: sqlite3.Connection = Depends(get_db)):
    return {"screen": kiosk.settings_of(_screen_row(conn, session["screen"])), "admin": session["admin"]["name"]}


class ScreenSettings(BaseModel):
    top_bar: bool


@router.put("/api/media/kiosk/screen")
def change_screen(body: ScreenSettings, session=Depends(kiosk_session), conn: sqlite3.Connection = Depends(get_db)):
    """What the box's settings page may change on the server: the top bar, for now."""
    row = _screen_row(conn, session["screen"])
    admin = {"id": session["admin"]["id"], "username": session["admin"]["name"]}
    with transaction(conn):
        kiosk.set_top_bar(conn, row["id"], body.top_bar)
        _log(conn, admin, "kiosk.top_bar", row["name"], "on" if body.top_bar else "off")
    return {"screen": kiosk.settings_of(_screen_row(conn, row["id"]))}


@router.post("/api/media/kiosk/lock", status_code=status.HTTP_204_NO_CONTENT)
def lock(request: Request, x_kiosk_session: str = Header("", max_length=200)):
    unlocks(request).end(x_kiosk_session)
