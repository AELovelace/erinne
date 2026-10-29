"""Preferences: everyone's own account page, in two halves.

Erynne (left): pick a theme, change your password, sign out other browsers,
link Microsoft 365 accounts (m365.js, web/m365.py), and see and forget what Erynne
remembers about you (web/memories.py). /settings and /memories redirect here.
Claude (right): ask Claude in chat with your own Anthropic API key (Claude
group only, claude_chat.py), and use Erynne from Claude: connectors, tokens
and what Claude can reach (connect.js, claude_connect.js, web/tokens.py)."""

from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from mnemerynne import accounts, api_tokens, apis, auth, claude_chat, memory, scratchpad, sync, users
from mnemerynne.clients import claude as claude_client
from mnemerynne.db import transaction
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import api_user, get_db, is_admin, page_user

router = APIRouter()

ADMIN_VIA = {"role": "local admin", "bootstrap": "admin (server setting)", "domain": "admin (domain group)"}


@router.get("/settings")
def old_settings_url(request: Request):
    """Old links and bookmarks. The query string comes along; the browser keeps the #fragment itself."""
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/preferences{query}", status_code=status.HTTP_308_PERMANENT_REDIRECT)


@router.get("/preferences", response_class=HTMLResponse)
async def preferences_page(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
    state = request.app.state
    via = accounts.admin_via(conn, user, state.directory, state.settings.admin_usernames)
    groups = [r[0] for r in conn.execute(
        "SELECT g.name FROM group_members m JOIN groups g ON g.id = m.group_id WHERE m.user_id = ? "
        "ORDER BY g.name COLLATE NOCASE", (user["id"],))]
    from mnemerynne.web.theme import looks_for
    from mnemerynne.web.tokens import extension_path

    looks = looks_for(request.app)
    return state.templates.TemplateResponse(request, "preferences.html", {
        "user": user, "is_admin": is_admin(request, conn, user), "active": "preferences",
        "themes": looks.pickable(), "theme_enforced": bool(looks.settings.get("enforce")),
        "current_theme": looks.resolve(user["theme"] or None, None).slug,
        "memories": await memory.list_memories(state.store, user["id"]), "memory_mode": state.settings.memory_mode,
        "scratchpad": scratchpad.summary(conn, user["id"], state.settings.scratchpad_claim_minutes),   # the agents' notes
        "admin_via": ADMIN_VIA.get(via or ""), "groups": groups, "domain": state.settings.ad_domain,
        "tokens": api_tokens.has_active(conn, user["id"]), "min_password": auth.MIN_PASSWORD_LENGTH,
        "has_extension": extension_path(state.settings) is not None,
        "claude_chat": claude_chat.is_claude_user(conn, user, state.directory),
        "backups": sync.may_backup(conn, user, state.directory)})


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


THEME_COOKIE = "erynne_theme"   # so the sign-in page matches (web/app.py _nav_context)


class ThemeBody(BaseModel):
    theme: str = Field(min_length=1, max_length=40)


@router.put("/api/me/theme")
def set_theme(body: ThemeBody, request: Request, response: Response, user=Depends(api_user),
              conn: sqlite3.Connection = Depends(get_db)):
    """Pick one of the themes an admin turned on (Settings → Themes). With one theme enforced there
    is nothing to pick."""
    from mnemerynne.web.theme import looks_for

    looks = looks_for(request.app)
    if looks.settings.get("enforce"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Your organisation's theme is set by an admin.")
    if body.theme not in {t.slug for t in looks.pickable()}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That theme isn't on offer here.")
    conn.execute("UPDATE users SET theme = ? WHERE id = ?", (body.theme, user["id"]))
    response.set_cookie(THEME_COOKIE, body.theme, max_age=365 * 86400, samesite="lax", httponly=True)
    return {"theme": body.theme, "look": looks.themes[body.theme].as_json()}


@router.post("/api/me/sign-out-others")
def sign_out_others(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """End every other browser's session (Claude tokens are separate: see the Claude page)."""
    conn.execute("UPDATE users SET session_epoch = session_epoch + 1 WHERE id = ?", (user["id"],))
    _keep_this_session(request, conn, user["id"])
    return {"status": "signed out"}


# ------------------------------------------------------------ Claude in chat
class ClaudeKeyBody(BaseModel):
    key: str | None = Field(None, max_length=400)   # omitted: change the model only
    model: str = Field(claude_chat.DEFAULT_MODEL, max_length=60)


def _claude_status(request: Request, conn: sqlite3.Connection, user) -> dict:
    settings = request.app.state.settings
    return {"allowed": claude_chat.is_claude_user(conn, user, request.app.state.directory),
            "secret_key_set": bool(settings.secret_key),
            "models": [{"id": k, "name": v} for k, v in claude_chat.MODELS.items()],
            **claude_chat.status(conn, settings, user["id"])}


@router.get("/api/me/claude")
def get_claude(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    return _claude_status(request, conn, user)


@router.put("/api/me/claude")
async def set_claude(body: ClaudeKeyBody, request: Request, user=Depends(api_user),
                     conn: sqlite3.Connection = Depends(get_db)):
    """Save your own Anthropic API key (checked with Anthropic first) and model. Never sent back."""
    if not claude_chat.is_claude_user(conn, user, request.app.state.directory):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Claude in chat isn't turned on for you.")
    try:
        model = claude_chat.check_model(body.model)
        key = claude_chat.check_shape(body.key) if body.key else None
        if key:
            await claude_client.check_key(key, model)
    except ValueError as exc:   # ClaudeKeyError too
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    try:
        with transaction(conn):
            if key:
                claude_chat.save_key(conn, request.app.state.settings, user["id"], key, model)
                _log(conn, user, "claude.key", user["username"], f"saved their own, {model}")
            elif not claude_chat.save_model(conn, user["id"], model):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Paste your API key first.")
    except apis.ApiError as exc:   # no MNEMERYNNE_SECRET_KEY on the server
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return _claude_status(request, conn, user)


@router.delete("/api/me/claude")
def remove_claude(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Forget your key (allowed even after leaving the Claude group)."""
    with transaction(conn):
        if claude_chat.remove_key(conn, user["id"]):
            _log(conn, user, "claude.key", user["username"], "removed their own")
    return _claude_status(request, conn, user)
