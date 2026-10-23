"""Links Claude can fetch (claude_links.py): the public /c/<id> address, which
needs no sign-in (Claude's web fetch runs at Anthropic), and the admin settings."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from mnemerynne import claude_links
from mnemerynne.db import transaction
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import admin_user, get_db

router = APIRouter()

# Nothing kept, indexed or passed on, and nothing in it runs if a browser opens it.
_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow, noarchive",
            "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox"}
_GONE = "This link has expired or doesn't exist. Search Mnemerynne again for a new one.\n"


@router.get("/c/{link_id}", include_in_schema=False)
def fetch(link_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    def number(name: str) -> int | None:
        value = request.query_params.get(name) or ""
        return int(value) if value.isdigit() and len(value) < 7 else None

    base = request.app.state.settings.public_url.rstrip("/")
    try:
        with transaction(conn):
            text = claude_links.read(conn, base, link_id[:64], number("from"), number("page"))
    except claude_links.TooMany:
        return PlainTextResponse("This link has been read too many times. Search Mnemerynne again for a new one.\n",
                                 status.HTTP_429_TOO_MANY_REQUESTS, headers=_HEADERS)
    except claude_links.LinkError:
        return PlainTextResponse(_GONE, status.HTTP_404_NOT_FOUND, headers=_HEADERS)
    return PlainTextResponse(text, headers=_HEADERS, media_type="text/markdown")


class LinkSettings(BaseModel):
    enabled: bool | None = None
    minutes: int | None = None


def _view(request: Request, conn: sqlite3.Connection) -> dict:
    return {**claude_links.config(conn), "public_url": request.app.state.settings.public_url,
            "limits": {"minutes": claude_links.MINUTES, "fetches": claude_links.MAX_FETCHES}}


@router.get("/api/admin/settings/claude-links")
def get_settings(request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return _view(request, conn)


@router.put("/api/admin/settings/claude-links")
def put_settings(body: LinkSettings, request: Request, user=Depends(admin_user),
                 conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            cfg = claude_links.save_config(conn, body.enabled, body.minutes)
            _log(conn, user, "settings.claude_links", "on" if cfg["enabled"] else "off", f"{cfg['minutes']} minutes")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return _view(request, conn)
