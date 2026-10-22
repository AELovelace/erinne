"""The response behind an API citation: what Erynne read when it answered."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from mnemerynne.web.deps import get_db, is_admin, page_user

router = APIRouter()


@router.get("/apis/calls/{call_id}", response_class=HTMLResponse)
def api_call_view(call_id: int, request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
    """The person who asked, and admins, can see it; everyone else gets 404."""
    call = conn.execute("SELECT * FROM api_calls WHERE id = ?", (call_id,)).fetchone()
    if call is None or (call["user_id"] != user["id"] and not is_admin(request, conn, user)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    response = request.app.state.templates.TemplateResponse(request, "api_call.html", {"call": call})
    response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'self'; frame-ancestors 'self'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
