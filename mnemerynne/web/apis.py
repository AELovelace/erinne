"""The response behind an API citation: what Erynne read when it answered."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from mnemerynne import apis
from mnemerynne.users import get_user_by_id
from mnemerynne.web.deps import LoginRequired, _session_user, get_db, is_admin

router = APIRouter()


@router.get("/apis/calls/{call_id}", response_class=HTMLResponse)
def api_call_view(call_id: int, request: Request, k: str = "", conn: sqlite3.Connection = Depends(get_db)):
    """The person who asked, and admins, can see it; everyone else gets 404. A link given to Claude
    carries a view key (apis.view_key) that opens it without signing in, while its owner's account is active."""
    call = conn.execute("SELECT * FROM api_calls WHERE id = ?", (call_id,)).fetchone()
    if k and call is not None and apis.check_view_key(request.app.state.settings, call_id, k):
        owner = get_user_by_id(conn, call["user_id"]) if call["user_id"] else None
        if owner is None or not owner["active"]:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    else:
        user = _session_user(request, conn)
        if user is None:
            raise LoginRequired()
        if call is None or (call["user_id"] != user["id"] and not is_admin(request, conn, user)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    response = request.app.state.templates.TemplateResponse(request, "api_call.html", {"call": call})
    response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'self'; frame-ancestors 'self'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # The key is in the address: keep it out of Referer headers and caches.
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response
