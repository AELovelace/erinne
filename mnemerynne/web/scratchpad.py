"""The Scratchpad page: people read their agents' boards and write on them (scratchpad.py).

The agents write and read through the connector (mcp_server: read_scratchpad, write_scratchpad, claim_note,
resolve_note). Here the person sees every board, note, claim and session, and writes directions: notes with
author 'person', for one agent label or everyone on the board, which reach each session with its next connector
call. Notes that fall off (cleared, deleted, past scratchpad_days) are kept in the archive, searchable here.

/api/scratchpad/directions is for the Claude Code hook (clients/claude-code-hooks): a personal token, not the
session cookie, and it only reads directions.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from mnemerynne import api_tokens, oauth, scratchpad, users
from mnemerynne.db import transaction
from mnemerynne.ingest import markdown
from mnemerynne.web.deps import api_user, get_db, is_admin, page_user

router = APIRouter()


def _refuse(exc: scratchpad.ScratchpadError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _minutes(request: Request) -> int:
    return request.app.state.settings.scratchpad_claim_minutes


@router.get("/scratchpad", response_class=HTMLResponse)
def scratchpad_page(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
    return request.app.state.templates.TemplateResponse(request, "scratchpad.html", {
        "user": user, "is_admin": is_admin(request, conn, user), "active": "scratchpad",
        "max_body": scratchpad.MAX_BODY})


@router.get("/api/scratchpad")
def scratchpad_summary(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Open notes per board and the live claims, as the briefing reports them."""
    return scratchpad.summary(conn, user["id"], _minutes(request))


@router.delete("/api/scratchpad")
def clear_scratchpad(board: str | None = None, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Take the person's notes off the scratchpad: one board's (`board`), or all of them. They stay in the
    archive. Theirs only: the query never reaches another person's rows."""
    try:
        return {"status": "archived", "count": scratchpad.clear(conn, user["id"], board)}
    except scratchpad.ScratchpadError as exc:
        raise _refuse(exc) from None


@router.get("/api/scratchpad/feed")
def feed(request: Request, board: str | None = None, after: str | None = None, user=Depends(api_user),
         conn: sqlite3.Connection = Depends(get_db)):
    """The page's poll: boards, and with `board` its notes (changed since `after`), sessions, and notes gone."""
    try:
        out = scratchpad.feed(conn, user["id"], board, after, _minutes(request))
    except scratchpad.ScratchpadError as exc:
        raise _refuse(exc) from None
    for n in out.get("notes", []):
        n["html"] = markdown.render_html(n["text"])     # raw HTML escaped, links allowlisted (as in Rooms)
    return out


class NoteBody(BaseModel):
    board: str = Field(..., max_length=scratchpad.MAX_BOARD)
    text: str = Field(..., max_length=scratchpad.MAX_BODY)
    for_agent: str | None = Field(None, max_length=scratchpad.MAX_AGENT)
    reply_to: int | None = None
    replaces: int | None = None
    urgent: bool = False


@router.post("/api/scratchpad/notes", status_code=status.HTTP_201_CREATED)
def write_note(body: NoteBody, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """A note from the person: a direction for one agent or everyone on the board."""
    try:
        if body.replaces is not None and scratchpad._get(conn, user["id"], body.replaces)["author"] != "person":
            raise scratchpad.ScratchpadError("Only your own notes can be edited; reply to an agent's instead.")
        note_id = scratchpad.write(conn, user["id"], body.board, body.text, user["username"], "web",
                                   replaces=body.replaces, author="person", for_agent=body.for_agent,
                                   reply_to=body.reply_to, urgent=body.urgent)
    except scratchpad.ScratchpadError as exc:
        raise _refuse(exc) from None
    return {"note_id": note_id, "board": scratchpad.board_key(body.board)}


class NoteChange(BaseModel):
    action: str = Field(..., pattern="^(resolve|reopen|release)$")


@router.patch("/api/scratchpad/notes/{note_id}")
def change_note(note_id: int, body: NoteChange, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Resolve, reopen, or release whoever's claim is on it."""
    try:
        if body.action == "release":
            scratchpad._get(conn, user["id"], note_id)
            with transaction(conn):
                conn.execute("UPDATE scratchpad_notes SET claimed_by = NULL, claimed_at = NULL, "
                             "updated_at = datetime('now') WHERE id = ? AND user_id = ?", (note_id, user["id"]))
            return scratchpad._row(scratchpad._get(conn, user["id"], note_id))
        return scratchpad.resolve(conn, user["id"], note_id, done=body.action == "resolve")
    except scratchpad.ScratchpadError as exc:
        raise _refuse(exc) from None


@router.delete("/api/scratchpad/notes/{note_id}")
def delete_note(note_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Take one note off its board (it stays in the archive)."""
    try:
        scratchpad.delete(conn, user["id"], note_id)
    except scratchpad.ScratchpadError as exc:
        raise _refuse(exc) from None
    return {"status": "archived"}


@router.get("/api/scratchpad/archive")
def archive(q: str = Query("", max_length=200), board: str | None = None, limit: int = Query(50, ge=1, le=200),
            user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Notes that fell off the scratchpad, newest first; `q` matches text, agent or board."""
    try:
        notes = scratchpad.search_archive(conn, user["id"], q, board, limit)
    except scratchpad.ScratchpadError as exc:
        raise _refuse(exc) from None
    for n in notes:
        n["html"] = markdown.render_html(n["text"])
    return {"notes": notes}


# --------------------------------------------------------- Claude Code hook
@router.get("/api/scratchpad/directions")
def directions(request: Request, agent: str = Query(..., max_length=scratchpad.MAX_AGENT),
               board: str | None = Query(None, max_length=scratchpad.MAX_BOARD), event: str = Query("", max_length=40),
               conn: sqlite3.Connection = Depends(get_db)):
    """For the Claude Code hook, with the person's own Claude token (Bearer): the directions this agent hasn't
    seen yet (marked seen now), and every one still open for it (`open`), so a session can't stop with one
    unanswered. `board` counts the session as working there; `event` is the hook that asked (for the page)."""
    auth = request.headers.get("authorization", "")
    value = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    caller = api_tokens.verify(conn, value) or oauth.verify(conn, value)
    user = users.get_user_by_id(conn, caller.user_id) if caller else None
    if user is None or not user["active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in: give the hook a Claude token.",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        label = scratchpad.agent_label(agent)
        connection = f"t{caller.token_id}" if caller.token_id else f"g{caller.grant_id}"
        with transaction(conn):
            scratchpad.touch_session(conn, user["id"], connection, label, f"hook:{event or 'check'}", board)
            fresh = scratchpad.directions_for(conn, user["id"], label)
        still_open = scratchpad.open_directions(conn, user["id"], label, board=board)
    except scratchpad.ScratchpadError as exc:
        raise _refuse(exc) from None
    return {"agent": label, "new": fresh, "open": still_open}
