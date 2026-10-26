"""Microsoft 365: people link their accounts on the Settings page; admins add
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
    return RedirectResponse(f"/preferences?m365={'linked' if ok else 'error'}&note={quote(note[:300])}#m365",
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
    from mnemerynne import sharepoint

    try:
        label = m365.tenant(conn, row_id)["label"]
    except m365.M365Error as exc:
        raise _refused(exc) from None
    for drive in conn.execute("SELECT id FROM sharepoint_drives WHERE tenant_row = ?", (row_id,)).fetchall():
        sharepoint.remove_library(conn, request.app.state.settings, drive["id"])
    with transaction(conn):
        m365.delete_tenant(conn, row_id)
        _log(conn, user, "m365.tenant_remove", label, "with every account linked and SharePoint library in it")
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


class GroupEntraBody(BaseModel):
    group: str = Field(default="", max_length=300)   # "Tenant / Group" or an entra: key; '' unlinks


@router.put("/api/admin/groups/{group_id}/entra")
def link_group_to_entra(group_id: int, body: GroupEntraBody, user=Depends(admin_user),
                        conn: sqlite3.Connection = Depends(get_db)):
    """A Mnemerynne group also takes in everyone in one Entra group (library grants follow)."""
    from mnemerynne import group_refs

    row = conn.execute("SELECT name FROM groups WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such group")
    text = body.group.strip()
    try:
        ref = group_refs.resolve(conn, text, []) if text else ""
    except group_refs.GroupRefError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    if ref and (";" in ref or not ref.startswith(group_refs.PREFIX)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Pick one Entra group, as “Tenant / Group” from the list.")
    with transaction(conn):
        conn.execute("UPDATE groups SET entra_key = ? WHERE id = ?", (ref or None, group_id))
        _log(conn, user, "group.entra", row["name"], group_refs.label(conn, ref) or "unlinked")
    return {"id": group_id, "entra": group_refs.label(conn, ref) or None}


@router.get("/api/admin/entra-groups")
def entra_groups(user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Every Entra group, as "Tenant / Group" (refreshed by the worker every few minutes)."""
    from mnemerynne import group_refs

    return {"groups": group_refs.choices(conn, [])}


# --------------------------------------------------------------- SharePoint
class SiteBody(BaseModel):
    tenant: int
    url: str = Field(min_length=10, max_length=500)
    drive_ids: list[str] = []


def _sync_in_background(settings, drive_rows: list[int]) -> None:
    """Fill or refresh libraries now instead of at the worker's next round."""
    import threading

    from mnemerynne import db, sharepoint

    def run():
        conn = db.connect(settings.db_path)
        try:
            for row in drive_rows:
                sharepoint.sync_drive(conn, settings, row)
        finally:
            conn.close()
    threading.Thread(target=run, name="sharepoint-sync", daemon=True).start()


@router.get("/api/admin/sharepoint")
def sharepoint_libraries(user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    from mnemerynne import sharepoint

    return {"libraries": sharepoint.libraries(conn),
            "tenants": [{"id": t["id"], "label": t["label"]} for t in m365.tenants(conn, enabled_only=True)]}


@router.post("/api/admin/sharepoint/resolve")
async def sharepoint_resolve(body: SiteBody, request: Request, user=Depends(admin_user),
                             conn: sqlite3.Connection = Depends(get_db)):
    """A site's document libraries, to pick from."""
    from mnemerynne import sharepoint

    try:
        return await anyio.to_thread.run_sync(lambda: sharepoint.resolve_site(
            conn, request.app.state.settings, body.tenant, body.url))
    except m365.M365Error as exc:
        raise _refused(exc) from None


@router.post("/api/admin/sharepoint/libraries", status_code=status.HTTP_201_CREATED)
async def sharepoint_add(body: SiteBody, request: Request, user=Depends(admin_user),
                         conn: sqlite3.Connection = Depends(get_db)):
    from mnemerynne import sharepoint

    settings = request.app.state.settings
    try:
        made = await anyio.to_thread.run_sync(lambda: sharepoint.add_libraries(
            conn, settings, body.tenant, body.url, body.drive_ids))
    except m365.M365Error as exc:
        raise _refused(exc) from None
    names = [r[0] for r in conn.execute(f"SELECT name FROM collections WHERE id IN ({','.join('?' * len(made))})",
                                        made)]
    _log(conn, user, "sharepoint.add", ", ".join(names), body.url)
    _sync_in_background(settings, [r[0] for r in conn.execute(
        f"SELECT id FROM sharepoint_drives WHERE collection_id IN ({','.join('?' * len(made))})", made)])
    return {"added": made, "libraries": sharepoint.libraries(conn)}


@router.post("/api/admin/sharepoint/{drive_row}/sync")
def sharepoint_sync_now(drive_row: int, request: Request, user=Depends(admin_user),
                        conn: sqlite3.Connection = Depends(get_db)):
    if conn.execute("SELECT 1 FROM sharepoint_drives WHERE id = ?", (drive_row,)).fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such SharePoint library.")
    _sync_in_background(request.app.state.settings, [drive_row])
    return {"status": "syncing"}


@router.delete("/api/admin/sharepoint/{drive_row}")
def sharepoint_remove(drive_row: int, request: Request, user=Depends(admin_user),
                      conn: sqlite3.Connection = Depends(get_db)):
    """Stop syncing a library and remove its copy from Mnemerynne (SharePoint itself is untouched)."""
    from mnemerynne import sharepoint

    removed = sharepoint.remove_library(conn, request.app.state.settings, drive_row)
    if removed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such SharePoint library.")
    _log(conn, user, "sharepoint.remove", removed[0], f"{removed[1]} files")
    return {"status": "removed"}


# ---------------------------------------------------------- shared mailboxes
class MailboxBody(BaseModel):
    tenant: int
    address: str = Field(min_length=3, max_length=200)
    label: str = Field(min_length=1, max_length=80)
    group: str = Field(default="", max_length=2000)   # who may ask Erynne about it ('' = anyone Exchange lets in)


def _mailboxes(conn: sqlite3.Connection) -> dict:
    from mnemerynne import group_refs

    rows = conn.execute("SELECT b.*, t.label AS tenant FROM m365_shared_mailboxes b JOIN m365_tenants t "
                        "ON t.id = b.tenant_row ORDER BY t.label COLLATE NOCASE, b.label COLLATE NOCASE").fetchall()
    return {"mailboxes": [{"id": r["id"], "tenant": r["tenant"], "address": r["address"], "label": r["label"],
                           "group": group_refs.label(conn, r["group_ref"])} for r in rows]}


@router.get("/api/admin/m365/mailboxes")
def list_mailboxes(user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return _mailboxes(conn)


@router.post("/api/admin/m365/mailboxes", status_code=status.HTTP_201_CREATED)
async def add_mailbox(body: MailboxBody, request: Request, user=Depends(admin_user),
                      conn: sqlite3.Connection = Depends(get_db)):
    from mnemerynne import group_refs

    try:
        m365.tenant(conn, body.tenant)
    except m365.M365Error as exc:
        raise _refused(exc) from None
    address = body.address.strip().lower()
    if "@" not in address or " " in address:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter the mailbox's email address.")
    directory = request.app.state.directory
    try:
        group = group_refs.resolve(conn, body.group, await anyio.to_thread.run_sync(directory.groups)
                                   if directory.enabled else [])
    except group_refs.GroupRefError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    try:
        with transaction(conn):
            conn.execute("INSERT INTO m365_shared_mailboxes(tenant_row, address, label, group_ref) VALUES (?, ?, ?, ?)",
                         (body.tenant, address, body.label.strip(), group))
            _log(conn, user, "m365.mailbox_add", body.label.strip(), address)
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "That mailbox is already listed.") from None
    return _mailboxes(conn)


@router.delete("/api/admin/m365/mailboxes/{mailbox_id}")
def remove_mailbox(mailbox_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute("SELECT label FROM m365_shared_mailboxes WHERE id = ?", (mailbox_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such mailbox.")
    with transaction(conn):
        conn.execute("DELETE FROM m365_shared_mailboxes WHERE id = ?", (mailbox_id,))
        _log(conn, user, "m365.mailbox_remove", row["label"])
    return _mailboxes(conn)
