"""The Messages page and what feeds the top bar: a person's notes and system messages
(mnemerynne/messages.py), the announcements meant for them (mnemerynne/notices.py), and the
unread count notify.js polls."""

from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from mnemerynne import messages, notices, rooms
from mnemerynne.web.deps import api_user, get_db, is_admin, page_user

router = APIRouter()


@router.get("/messages", response_class=HTMLResponse)
def messages_page(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
    return request.app.state.templates.TemplateResponse(request, "messages.html", {
        "user": user, "is_admin": is_admin(request, conn, user), "active": "messages",
        "max_chars": messages.MAX_CHARS})


@router.get("/api/people")
def list_people(user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Who a note can go to: everyone who can sign in (names only)."""
    return {"people": messages.people(conn, except_user_id=user["id"])}


@router.get("/api/messages")
def list_messages(box: Literal["inbox", "sent"] = "inbox", unread: bool = False, limit: int = messages.MAX_LISTED,
                  user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    listed = (messages.sent(conn, user["id"], limit) if box == "sent"
              else messages.inbox(conn, user["id"], unread_only=unread, limit=limit))
    return {"messages": listed, "unread": messages.unread_count(conn, user["id"])}


class NoteBody(BaseModel):
    to: str = Field(min_length=1, max_length=64)
    text: str = Field(max_length=messages.MAX_CHARS)
    link: str | None = Field(None, max_length=messages.MAX_LINK)


@router.post("/api/messages", status_code=status.HTTP_201_CREATED)
def send_message(body: NoteBody, request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    try:
        message_id = messages.send(conn, user, body.to, body.text, via="web", link=body.link,
                                   public_url=request.app.state.settings.public_url)
    except messages.MessageError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return {"message": next(m for m in messages.sent(conn, user["id"], 5) if m["message_id"] == message_id)}


@router.post("/api/messages/read-all")
def read_all(user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    return {"read": messages.mark_all_read(conn, user["id"])}


@router.post("/api/messages/{message_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def read_message(message_id: int, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    if not messages.mark_read(conn, user["id"], message_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such message")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------ the top bar
@router.get("/api/notify")
def notify(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """What the badge and the announcement cards need; polled by notify.js on every page."""
    return {"unread": messages.unread_count(conn, user["id"]),
            "rooms": rooms.unread_summary(conn, user, request.app.state.directory),
            "announcements": notices.pending_for(conn, user, request.app.state.directory)}


@router.get("/api/announcements")
def my_announcements(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Every announcement meant for this person, seen or not (the Messages page)."""
    return {"announcements": notices.active_for(conn, user, request.app.state.directory)}


class MarkBody(BaseModel):
    ack: bool = False


@router.post("/api/announcements/{announcement_id}/mark", status_code=status.HTTP_204_NO_CONTENT)
def mark_announcement(announcement_id: int, body: MarkBody | None = None, request: Request = None,
                      user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    try:
        notices.mark(conn, user, request.app.state.directory, announcement_id, body.ack if body else False)
    except notices.NoticeError as exc:
        code = status.HTTP_404_NOT_FOUND if "No such" in str(exc) else status.HTTP_422_UNPROCESSABLE_CONTENT
        raise HTTPException(code, str(exc)) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
