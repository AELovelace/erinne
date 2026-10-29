"""OAuth endpoints for /mcp (mnemerynne/oauth.py): discovery documents, app
registration, the sign-in and consent page, tokens, the connections people
and admins can see and disconnect, and the admin switch for ChatGPT."""

from __future__ import annotations

import json
import secrets
import sqlite3
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from mnemerynne import oauth, permissions, sync
from mnemerynne.db import transaction
from mnemerynne.mcp_server import public_base
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import _session_user, admin_user, api_user, get_db

router = APIRouter()
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _base(request: Request) -> str:
    headers = {k.lower(): v for k, v in request.headers.items()}
    return public_base(request.app.state.settings, headers, {"scheme": request.url.scheme})


def _error(exc: oauth.OAuthError) -> JSONResponse:
    return JSONResponse(exc.body(), status_code=exc.status, headers=NO_STORE)


# ---------------------------------------------------------------- discovery
@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource(request: Request):
    """RFC 9728: /mcp, and who issues tokens for it (this server)."""
    base = _base(request)
    return {"resource": base + "/mcp", "authorization_servers": [base], "bearer_methods_supported": ["header"],
            "scopes_supported": ["mcp"], "resource_name": "Mnemerynne"}


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp")
def authorization_server(request: Request):
    """RFC 8414. RFC 9207: every redirect back carries `iss` (this issuer), which lets ChatGPT use its
    stable callback."""
    base = _base(request)
    return {"issuer": base, "authorization_response_iss_parameter_supported": True,
            "authorization_endpoint": base + "/oauth/authorize",
            "token_endpoint": base + "/oauth/token", "registration_endpoint": base + "/oauth/register",
            "revocation_endpoint": base + "/oauth/revoke", "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"], "scopes_supported": ["mcp"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "revocation_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"]}


# ------------------------------------------------------------- registration
@router.post("/oauth/register", status_code=status.HTTP_201_CREATED)
async def register(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    try:
        body = await request.json()
    except ValueError:
        return _error(oauth.OAuthError("invalid_client_metadata", "Send the registration as JSON."))
    try:
        out = oauth.register(conn, body if isinstance(body, dict) else {})
    except oauth.OAuthError as exc:
        return _error(exc)
    return JSONResponse(out, status_code=status.HTTP_201_CREATED, headers=NO_STORE)


# ------------------------------------------------------------------ consent
def _page(request: Request, template: str, code: int = 200, **context) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, template, context, status_code=code)


@router.get("/oauth/authorize", response_class=HTMLResponse)
def authorize(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    params = dict(request.query_params)
    try:
        row, redirect_uri = oauth.check_request(conn, params, _base(request) + "/mcp")
    except oauth.OAuthError as exc:   # never redirect to an address that didn't check out
        return _page(request, "oauth_consent.html", 400, error=exc.description, user=None)
    user = _session_user(request, conn)
    if user is None:
        request.session["after_login"] = str(request.url.path) + "?" + str(request.url.query)
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    nonce = secrets.token_urlsafe(24)
    scope = "sync" if params.get("scope") == "sync" else "mcp"
    request.session["oauth_consent"] = {"nonce": nonce, "user_id": user["id"], "client_id": row["client_id"],
                                        "redirect_uri": redirect_uri, "challenge": params["code_challenge"],
                                        "state": params.get("state"), "scope": scope}
    if scope == "sync":
        # The desktop backup app (sync.py): only an app on the person's own computer, and only for people who may.
        if oauth.kind(json.loads(row["redirect_uris"])) != "local":
            request.session.pop("oauth_consent", None)
            return _page(request, "oauth_consent.html", 400, user=None,
                         error="Only the backup app on your own computer can ask for that.")
        if not sync.may_backup(conn, user, request.app.state.directory):
            request.session.pop("oauth_consent", None)
            return _page(request, "oauth_consent.html", 403, user=None,
                         error="Backups aren't open for you; ask an admin.")
        return _page(request, "oauth_consent.html", user=user, nonce=nonce, scope=scope, name=row["name"],
                     host=urlsplit(redirect_uri).hostname, error=None)
    shared = permissions.claude_collection_ids(conn, user["id"])
    places = [("📥 " + permissions.PRIVATE_LABEL if c["kind"] == "private" else "📚 " + c["name"])
              for c in permissions.readable_collections(conn, user["id"]) if c["id"] in shared]
    app, provider = oauth.APPS[oauth.kind(json.loads(row["redirect_uris"]))]
    return _page(request, "oauth_consent.html", user=user, nonce=nonce, places=places,
                 app=app, provider=provider, name=row["name"],
                 host=urlsplit(redirect_uri).hostname, error=None)


@router.post("/oauth/authorize")
async def decide(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    pending = request.session.pop("oauth_consent", None)
    user = _session_user(request, conn)
    if (not pending or user is None or pending["user_id"] != user["id"]
            or not secrets.compare_digest(str(pending["nonce"]), str(form.get("nonce", "")))):
        return _page(request, "oauth_consent.html", 400, user=None,
                     error="That approval didn't match; go back to the app and connect again.")
    query: dict = {"state": pending["state"]} if pending.get("state") else {}
    if form.get("decision") == "allow":
        row = oauth.client(conn, pending["client_id"])
        if row is None:
            return _page(request, "oauth_consent.html", 400, user=None, error="That app was removed; connect again.")
        scope = pending.get("scope", "mcp")
        query["code"] = oauth.issue_code(conn, row, user["id"], pending["redirect_uri"], pending["challenge"],
                                         scope == "mcp" and form.get("can_edit") == "1", scope)
    else:
        query["error"] = "access_denied"
    # RFC 9207 on success and error alike, exactly the metadata's issuer: ChatGPT rejects a reply without it.
    query["iss"] = _base(request)
    sep = "&" if "?" in pending["redirect_uri"] else "?"
    return RedirectResponse(pending["redirect_uri"] + sep + urlencode(query), status_code=status.HTTP_303_SEE_OTHER)


# -------------------------------------------------------------------- token
@router.post("/oauth/token")
async def token(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = {k: str(v) for k, v in (await request.form()).items()}
    auth = request.headers.get("authorization", "")
    basic = auth[6:].strip() if auth.lower().startswith("basic ") else None
    try:
        # No transaction around it: ending a connection whose code or refresh token
        # was used twice must stick even though the request itself is refused.
        out = oauth.token(conn, form, basic)
    except oauth.OAuthError as exc:
        return _error(exc)
    return JSONResponse(out, headers=NO_STORE)


@router.post("/oauth/revoke")
async def revoke(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    oauth.revoke_token(conn, str(form.get("token", "")))
    return JSONResponse({}, headers=NO_STORE)


# -------------------------------------------------------------- connections
class EditBody(BaseModel):
    can_edit: bool


@router.get("/api/oauth/connections")
def my_connections(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    return {"connections": oauth.connections(conn, user["id"]), "mcp_url": _base(request) + "/mcp"}


@router.patch("/api/oauth/connections/{grant_id}")
def set_edit(grant_id: int, body: EditBody, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    if not oauth.set_can_edit(conn, grant_id, user["id"], body.can_edit):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such connection.")
    return {"status": "saved"}


@router.delete("/api/oauth/connections/{grant_id}")
def disconnect(grant_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    if not oauth.revoke_grant(conn, grant_id, user["id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such connection.")
    return {"status": "disconnected"}


@router.get("/api/admin/users/{user_id}/connections")
def user_connections(user_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return {"connections": oauth.connections(conn, user_id)}


class ChatGPTBody(BaseModel):
    enabled: bool


@router.get("/api/admin/settings/chatgpt")
def get_chatgpt(request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Whether people may connect ChatGPT (it then sees what is shared with Claude), and how many have."""
    count = sum(1 for r in conn.execute("SELECT c.redirect_uris FROM oauth_grants g JOIN oauth_clients c "
                                        "ON c.id = g.client_row WHERE g.revoked_at IS NULL")
                if oauth.kind(json.loads(r["redirect_uris"])) == "chatgpt")
    return {"enabled": oauth.chatgpt_enabled(conn), "connections": count, "mcp_url": _base(request) + "/mcp"}


@router.put("/api/admin/settings/chatgpt")
def put_chatgpt(body: ChatGPTBody, request: Request, user=Depends(admin_user),
                conn: sqlite3.Connection = Depends(get_db)):
    with transaction(conn):
        ended = oauth.set_chatgpt(conn, body.enabled)
        _log(conn, user, "settings.chatgpt", "on" if body.enabled else "off",
             f"ended {ended} connection(s)" if ended else "")
    return get_chatgpt(request, user, conn)


@router.delete("/api/admin/oauth/connections/{grant_id}")
def admin_disconnect(grant_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute("SELECT u.username FROM oauth_grants g JOIN users u ON u.id = g.user_id WHERE g.id = ?",
                       (grant_id,)).fetchone()
    if row is None or not oauth.revoke_grant(conn, grant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such connection.")
    _log(conn, user, "oauth.disconnect", row["username"], f"connection {grant_id}")
    return {"status": "disconnected"}
