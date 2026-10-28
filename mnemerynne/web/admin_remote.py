"""Access → Remote: the LAN apps served through Mnemerynne (mnemerynne/remote.py): address, who may
open each, a pinned certificate for self-signed servers, and how the Remote tab shows it."""

from __future__ import annotations

import sqlite3
import ssl

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from mnemerynne import apis, group_refs, remote
from mnemerynne.db import transaction
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import admin_user, get_db

router = APIRouter(prefix="/api/admin/remote")


def _check_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url.startswith(("https://", "http://")) or " " in url or len(url) > 500:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The address must start with https:// (or http://).")
    return url


def _row(conn: sqlite3.Connection, app_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM remote_apps WHERE id = ?", (app_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such remote app")
    return row


def _json(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    return {"id": r["id"], "slug": r["slug"], "name": r["name"], "description": r["description"],
            "base_url": r["base_url"], "group": group_refs.label(conn, r["group_ref"]),
            "certificate": "pinned" if r["ca_pem"].strip() else "system", "tls_check_hostname": bool(r["tls_check_hostname"]),
            "rewrite_html": bool(r["rewrite_html"]), "embed": bool(r["embed"]), "enabled": bool(r["enabled"]),
            "look": r["xsl_override"] or "", "look_theme": r["look_theme"],
            "url": f"{remote.PREFIX}/{r['slug']}/", "created_at": r["created_at"]}


async def _listing(request: Request, conn: sqlite3.Connection) -> dict:
    directory = request.app.state.directory
    ad = await anyio.to_thread.run_sync(directory.groups) if directory.enabled else []
    rows = conn.execute("SELECT * FROM remote_apps ORDER BY name COLLATE NOCASE").fetchall()
    return {"apps": [_json(conn, r) for r in rows], "groups": group_refs.choices(conn, ad),
            "domain": request.app.state.settings.ad_domain, "looks": remote.available_looks()}


class NewApp(BaseModel):
    slug: str = Field(max_length=40)
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(max_length=500)


class AppPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    description: str | None = Field(None, max_length=1000)
    base_url: str | None = Field(None, max_length=500)
    group: str | None = Field(None, max_length=2000)
    ca_pem: str | None = Field(None, max_length=20000)
    tls_check_hostname: bool | None = None
    rewrite_html: bool | None = None
    embed: bool | None = None
    enabled: bool | None = None
    look: str | None = Field(None, max_length=20)          # '' = the app's own; a key of remote.OVERRIDES
    look_theme: str | None = Field(None, max_length=20)    # '' = the viewer's; or one of remote.LOOK_THEMES


@router.get("")
async def list_apps(request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return await _listing(request, conn)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_app(body: NewApp, request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    try:
        slug = remote.check_slug(body.slug)
    except remote.RemoteError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    url = _check_url(body.base_url)
    if remote.get_app(conn, slug) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There's already a remote app called {slug}.")
    with transaction(conn):
        app_id = conn.execute("INSERT INTO remote_apps(slug, name, base_url) VALUES (?, ?, ?)",
                              (slug, body.name.strip(), url)).lastrowid
        _log(conn, user, "remote.create", slug, url)
    return {"id": app_id, **await _listing(request, conn)}


@router.patch("/{app_id}")
async def update_app(app_id: int, body: AppPatch, request: Request, user=Depends(admin_user),
                     conn: sqlite3.Connection = Depends(get_db)):
    row = _row(conn, app_id)
    changes: dict[str, object] = {}
    if body.name is not None:
        changes["name"] = body.name.strip()
    if body.description is not None:
        changes["description"] = body.description.strip()
    if body.base_url is not None:
        changes["base_url"] = _check_url(body.base_url)
    if body.group is not None:
        if body.group.strip():
            directory = request.app.state.directory
            known = await anyio.to_thread.run_sync(directory.groups) if directory.enabled else []
            try:
                changes["group_ref"] = group_refs.resolve(conn, body.group, known)
            except group_refs.GroupRefError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
        else:
            changes["group_ref"] = ""
    if body.ca_pem is not None:
        pem = body.ca_pem.strip()
        if pem:
            try:
                ssl.create_default_context(cadata=pem)
            except (ssl.SSLError, ValueError):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That isn't a PEM certificate.") from None
        changes["ca_pem"] = pem
    for key in ("tls_check_hostname", "rewrite_html", "embed", "enabled"):
        value = getattr(body, key)
        if value is not None:
            changes[key] = int(value)
    if body.look is not None:
        if body.look and body.look not in remote.available_looks():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such look.")
        changes["xsl_override"] = body.look or None
    if body.look_theme is not None:
        if body.look_theme and body.look_theme not in remote.LOOK_THEMES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No such theme.")
        changes["look_theme"] = body.look_theme
    # A look is applied while the proxy transforms the pages, which it does only with rewriting on.
    if changes.get("xsl_override", row["xsl_override"]) and not changes.get("rewrite_html", row["rewrite_html"]):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "A look needs “Rewrite absolute links” on: the pages are only redrawn then.")
    if not changes:
        return await _listing(request, conn)
    with transaction(conn):
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"UPDATE remote_apps SET {sets}, updated_at = datetime('now') WHERE id = ?",
                     (*changes.values(), app_id))
        detail = ", ".join(f"{k} {'set' if k in ('ca_pem',) else v}" for k, v in changes.items()
                           if k not in ("description",))
        _log(conn, user, "remote.update", row["slug"], detail[:500])
    return await _listing(request, conn)


@router.post("/{app_id}/fetch-certificate")
async def fetch_certificate(app_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """The certificate the server presents (PEM and SHA-256 fingerprint), for the admin to compare and pin."""
    row = _row(conn, app_id)
    try:
        pem, fingerprint = await anyio.to_thread.run_sync(apis.fetch_certificate, row["base_url"])
    except apis.ApiError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return {"ca_pem": pem, "fingerprint": fingerprint}


@router.delete("/{app_id}")
async def delete_app(app_id: int, request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    row = _row(conn, app_id)
    with transaction(conn):
        conn.execute("DELETE FROM remote_apps WHERE id = ?", (app_id,))
        _log(conn, user, "remote.delete", row["slug"], row["base_url"])
    return await _listing(request, conn)
