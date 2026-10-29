"""The agent scratchpad on Preferences: people see how much their agents have jotted and can wipe it.

The notes themselves are written and read by the person's agents through the connector
(mcp_server: read_scratchpad, write_scratchpad, claim_note, resolve_note; the module is
mnemerynne/scratchpad.py). This is the person's own view: a count per board, and Clear."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from mnemerynne import scratchpad
from mnemerynne.web.deps import api_user, get_db

router = APIRouter()


@router.get("/api/scratchpad")
def scratchpad_summary(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Open notes per board and the live claims, as the briefing reports them."""
    return scratchpad.summary(conn, user["id"], request.app.state.settings.scratchpad_claim_minutes)


@router.delete("/api/scratchpad")
def clear_scratchpad(board: str | None = None, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Delete the person's notes: one board's (`board`), or all of them. Theirs only: the query never
    reaches another person's rows."""
    return {"status": "deleted", "count": scratchpad.clear(conn, user["id"], board)}
