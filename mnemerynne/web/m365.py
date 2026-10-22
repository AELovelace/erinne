"""Microsoft 365: people link their accounts on the Connect page; admins add
the tenants on Settings. Sign-in to Mnemerynne itself is unchanged."""

from __future__ import annotations

import sqlite3
from urllib.parse import quote

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from mnemerynne import m365
from mnemerynne.db import transaction
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import admin_user, api_user, get_db, page_user

router = APIRouter()


def _redirect_uri(request: Request) -> str:
    base = request.app.state.settings.public_url.rstrip("/") or str(request.base_url).rstrip("/")
    return base + "/auth/microsoft/callback"


def _refused(exc: m365.M365Error) -> HTTPException:
    return HTTPException(getattr(exc, "status", 400), str(exc))


def _back(note: str, ok: bool) -> RedirectResponse:
    return RedirectResponse(f"/connect?m365={'linked' if ok else 'error'}&note={quote(note[:300])}#m365",
                            status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------ linking
@router.get("/m365/link")
def link(request: Request, tenant: int | None = None, user=Depends(page_user),
         conn: sqlite3.Connection = Depends(get_db)):
    """Send the person to Microsoft to sign in with the account to link."""
    try:
        url = m365.begin_link(conn, request.app.state.settings, request.session, user["id"],
                              _redirect_uri(request), tenant)
    except m365.M365Error as exc:
        return _back(str(exc), False)
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/auth/microsoft/callback")
async def callback(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
    params = request.query_params
    if params.get("admin_consent"):   # a tenant admin just granted consent
        return RedirectResponse("/admin/libraries#tab=settings", status_code=status.HTTP_303_SEE_OTHER)
    if params.get("error"):
        request.session.pop("m365_link", None)
        reason = (params.get("error_description") or params["error"]).splitlines()[0][:200]
        return _back(f"Microsoft didn't link the account: {reason}", False)
    try:
        linked = await anyio.to_thread.run_sync(lambda: m365.finish_link(
            conn, request.app.state.settings, request.session, user, params.get("code", ""),
            params.get("state", ""), _redirect_uri(request)))
    except m365.M365Error as exc:
        return _back(str(exc), False)
    return _back(f"Linked {linked['upn']} ({linked['tenant']}).", True)


@router.get("/api/m365")
def my_accounts(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """The person's linked accounts, and the tenants they can link."""
    settings = request.app.state.settings
    shared = m365.configured(settings)
    tenants = [{"id": t["id"], "label": t["label"], "own_app": bool(t["client_id"])}
               for t in m365.tenants(conn, enabled_only=True) if shared or t["client_id"]]
    return {"available": bool(tenants), "accounts": m365.identities(conn, user["id"]), "tenants": tenants}


@router.delete("/api/m365/accounts/{identity_id}")
def unlink(identity_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    if not m365.unlink(conn, identity_id, user["id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such linked account.")
    return {"status": "unlinked"}


# -------------------------------------------------------------------- admin
class TenantBody(BaseModel):
    ref: str = Field(min_length=3, max_length=200)          # tenant id or a domain
    label: str = Field(default="", max_length=80)
    mode: str = "cloud"
    client_id: str = Field(default="", max_length=100)
    client_secret: str = Field(default="", max_length=500)


class TenantPatch(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    mode: str | None = None
    enabled: bool | None = None
    client_id: str | None = Field(default=None, max_length=100)
    client_secret: str | None = Field(default=None, max_length=500)   # '' removes it


def _tenants(request: Request, conn: sqlite3.Connection) -> dict:
    settings = request.app.state.settings
    out = []
    for t in m365.tenants(conn):
        try:
            consent = m365.consent_url(settings, t)
        except m365.M365Error:
            consent = None
        linked = conn.execute("SELECT count(*) FROM m365_identities WHERE tenant_row = ?", (t["id"],)).fetchone()[0]
        out.append({"id": t["id"], "tenant_id": t["tenant_id"], "label": t["label"], "domain": t["domain"],
                    "mode": t["mode"], "enabled": bool(t["enabled"]), "own_app": bool(t["client_id"]),
                    "client_id": t["client_id"], "secret_set": t["client_secret_enc"] is not None,
                    "consent_url": consent, "consent_ok_at": t["consent_ok_at"], "consent_error": t["consent_error"],
                    "linked": linked})
    return {"configured": m365.configured(settings), "redirect_uri": _redirect_uri(request), "tenants": out}


@router.get("/api/admin/m365")
def admin_tenants(request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return _tenants(request, conn)


@router.post("/api/admin/m365/tenants", status_code=status.HTTP_201_CREATED)
async def add_tenant(body: TenantBody, request: Request, user=Depends(admin_user),
                     conn: sqlite3.Connection = Depends(get_db)):
    settings = request.app.state.settings
    try:
        row_id = await anyio.to_thread.run_sync(lambda: m365.add_tenant(
            conn, settings, body.ref, body.label, body.mode, body.client_id, body.client_secret))
    except m365.M365Error as exc:
        raise _refused(exc) from None
    _log(conn, user, "m365.tenant_add", body.label or body.ref, f"{body.mode}, {body.ref}")
    return {"id": row_id, **_tenants(request, conn)}


@router.patch("/api/admin/m365/tenants/{row_id}")
def update_tenant(row_id: int, body: TenantPatch, request: Request, user=Depends(admin_user),
                  conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            m365.update_tenant(conn, request.app.state.settings, row_id, **body.model_dump())
            changed = ", ".join(k for k, v in body.model_dump().items() if v is not None and k != "client_secret")
            if body.client_secret is not None:
                changed += (", " if changed else "") + ("secret set" if body.client_secret else "secret removed")
            _log(conn, user, "m365.tenant", m365.tenant(conn, row_id)["label"], changed)
    except m365.M365Error as exc:
        raise _refused(exc) from None
    return _tenants(request, conn)


@router.delete("/api/admin/m365/tenants/{row_id}")
def delete_tenant(row_id: int, request: Request, user=Depends(admin_user),
                  conn: sqlite3.Connection = Depends(get_db)):
    try:
        label = m365.tenant(conn, row_id)["label"]
    except m365.M365Error as exc:
        raise _refused(exc) from None
    with transaction(conn):
        m365.delete_tenant(conn, row_id)
        _log(conn, user, "m365.tenant_remove", label, "and every account linked in it")
    return _tenants(request, conn)


@router.post("/api/admin/m365/tenants/{row_id}/check")
async def check_tenant(row_id: int, request: Request, user=Depends(admin_user),
                       conn: sqlite3.Connection = Depends(get_db)):
    """Try the app's own sign-in to the tenant: shows whether its admin consented."""
    try:
        result = await anyio.to_thread.run_sync(lambda: m365.check_consent(conn, request.app.state.settings, row_id))
    except m365.M365Error as exc:
        raise _refused(exc) from None
    return {**result, **_tenants(request, conn)}


@router.get("/api/admin/users/{user_id}/m365")
def user_accounts(user_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return {"accounts": m365.identities(conn, user_id)}


@router.delete("/api/admin/m365/accounts/{identity_id}")
def admin_unlink(identity_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute("SELECT i.upn, u.username FROM m365_identities i JOIN users u ON u.id = i.user_id "
                       "WHERE i.id = ?", (identity_id,)).fetchone()
    if row is None or not m365.unlink(conn, identity_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such linked account.")
    _log(conn, user, "m365.unlink", row["username"], row["upn"])
    return {"status": "unlinked"}
