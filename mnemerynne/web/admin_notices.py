"""Access → Announcements: admins post, change and remove announcements (mnemerynne/notices.py)."""

from __future__ import annotations

import sqlite3

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from mnemerynne import group_refs, notices
from mnemerynne.db import transaction
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import admin_user, get_db

router = APIRouter(prefix="/api/admin/announcements")


async def _known_groups(request: Request, conn: sqlite3.Connection) -> list[str]:
    directory = request.app.state.directory
    return await anyio.to_thread.run_sync(directory.groups) if directory.enabled else []


async def _listing(request: Request, conn: sqlite3.Connection) -> dict:
    ad = await _known_groups(request, conn)
    return {"announcements": notices.listing(conn), "groups": group_refs.choices(conn, ad),
            "domain": request.app.state.settings.ad_domain}


class NoticeBody(BaseModel):
    title: str = Field(max_length=notices.MAX_TITLE)
    body: str = Field("", max_length=notices.MAX_BODY)
    link: str = Field("", max_length=500)
    group: str = Field("", max_length=2000)      # group_refs text; empty = everyone
    must_ack: bool = False
    expires_at: str | None = Field(None, max_length=20)


class NoticePatch(BaseModel):
    title: str | None = Field(None, max_length=notices.MAX_TITLE)
    body: str | None = Field(None, max_length=notices.MAX_BODY)
    link: str | None = Field(None, max_length=500)
    group: str | None = Field(None, max_length=2000)
    must_ack: bool | None = None
    expires_at: str | None = Field(None, max_length=20)
    clear_expiry: bool = False


async def _group_ref(request: Request, conn: sqlite3.Connection, text: str) -> str:
    if not (text or "").strip():
        return ""
    try:
        return group_refs.resolve(conn, text, await _known_groups(request, conn))
    except group_refs.GroupRefError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.get("")
async def list_announcements(request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return await _listing(request, conn)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_announcement(body: NoticeBody, request: Request, user=Depends(admin_user),
                              conn: sqlite3.Connection = Depends(get_db)):
    group = await _group_ref(request, conn, body.group)
    try:
        with transaction(conn):
            announcement_id = notices.create(conn, user["id"], body.title, body.body, link=body.link, group_ref=group,
                                             must_ack=body.must_ack, expires_at=body.expires_at)
            _log(conn, user, "announcement.create", body.title.strip()[:120],
                 f"for {group_refs.label(conn, group) or 'everyone'}" + (", must acknowledge" if body.must_ack else ""))
    except notices.NoticeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return {"id": announcement_id, **await _listing(request, conn)}


@router.patch("/{announcement_id}")
async def update_announcement(announcement_id: int, body: NoticePatch, request: Request, user=Depends(admin_user),
                              conn: sqlite3.Connection = Depends(get_db)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None and k not in ("group", "clear_expiry")}
    if body.group is not None:
        fields["group_ref"] = await _group_ref(request, conn, body.group)
    if body.clear_expiry:
        fields["expires_at"] = None
    try:
        with transaction(conn):
            notices.update(conn, announcement_id, **fields)
            _log(conn, user, "announcement.update", notices.get(conn, announcement_id)["title"], ", ".join(fields))
    except notices.NoticeError as exc:
        code = status.HTTP_404_NOT_FOUND if "No such" in str(exc) else status.HTTP_422_UNPROCESSABLE_CONTENT
        raise HTTPException(code, str(exc)) from None
    return await _listing(request, conn)


@router.delete("/{announcement_id}")
async def delete_announcement(announcement_id: int, request: Request, user=Depends(admin_user),
                              conn: sqlite3.Connection = Depends(get_db)):
    try:
        title = notices.get(conn, announcement_id)["title"]
    except notices.NoticeError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such announcement") from None
    with transaction(conn):
        notices.delete(conn, announcement_id)
        _log(conn, user, "announcement.delete", title)
    return await _listing(request, conn)
