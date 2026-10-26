"""Rooms: the page, its API and its live stream (mnemerynne/rooms.py, rooms_bus.py).

Every route takes the signed-in person and checks again that they can see the room; writes go
through the CSRF guard as ordinary POST/PATCH/DELETE, and the stream (server-sent events) only
reads. After each committed write the bus is woken so open streams send it at once.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from mnemerynne import rooms
from mnemerynne.db import transaction
from mnemerynne.web import security
from mnemerynne.web.admin import _log
from mnemerynne import group_refs
from mnemerynne.web.deps import admin_user, api_user, get_db, is_admin, page_user



def _rooms_on(conn: sqlite3.Connection = Depends(get_db)) -> None:
    """Every Rooms route but the admin settings: not there at all while Rooms is turned off."""
    if not rooms.enabled(conn):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


router = APIRouter(dependencies=[Depends(_rooms_on)])
admin_router = APIRouter()   # Settings → Rooms: reachable when it's off, to turn it on
POSTS_PER_MINUTE = 30
TYPING_PER_MINUTE = 30


def _limiter(request: Request) -> security.RateLimiter:
    """One per app (posting and typing limits), made on first use."""
    if not hasattr(request.app.state, "rooms_limiter"):
        request.app.state.rooms_limiter = security.RateLimiter()
    return request.app.state.rooms_limiter


def _refuse(exc: rooms.RoomError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _bus(request: Request):
    return request.app.state.rooms_bus


def _dir(request: Request):
    return request.app.state.directory


def _done(request: Request) -> None:
    """Called after a write commits: streams send the new events now."""
    _bus(request).wake()


# --------------------------------------------------------------------- page
@router.get("/rooms", response_class=HTMLResponse)
def rooms_page(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
    return request.app.state.templates.TemplateResponse(request, "rooms.html", {
        "user": user, "is_admin": is_admin(request, conn, user), "active": "rooms", "max_chars": rooms.MAX_CHARS})


# -------------------------------------------------------------------- rooms
@router.get("/api/rooms")
def list_rooms(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """The sidebar, who's online, and the event id to start the stream from."""
    bus = _bus(request)
    return {"rooms": rooms.rooms_for(conn, user, _dir(request)), "presence": bus.presence(),
            "cursor": conn.execute("SELECT coalesce(max(id), 0) FROM chat_events").fetchone()[0],
            "me": user["username"]}


class PreviewBody(BaseModel):
    links: list[str] = Field(default_factory=list, max_length=20)


@router.post("/api/rooms/previews")
def link_previews(body: PreviewBody, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Cards for Mnemerynne links in messages, as this person may see them."""
    return {"previews": rooms.previews(conn, user, [link[:500] for link in body.links])}


@router.get("/api/rooms/files/{file_id}")
def get_file(file_id: int, request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    try:
        row, path = rooms.attachment(conn, request.app.state.settings, user, file_id, _dir(request),
                                     admin=is_admin(request, conn, user))
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    inline = row["mime"] in rooms.INLINE
    return FileResponse(path, media_type=row["mime"] if inline else "application/octet-stream",
                        filename=row["name"], content_disposition_type="inline" if inline else "attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"})


@router.post("/api/rooms/files/{file_id}/save")
def save_file(file_id: int, request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Copy an attachment into the person's own File box (Agents folder's parent: the top of it)."""
    from mnemerynne import permissions
    from mnemerynne.ingest import store

    settings = request.app.state.settings
    try:
        row, path = rooms.attachment(conn, settings, user, file_id, _dir(request))
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    inbox = permissions.private_collection_id(conn, user["id"])
    if inbox is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You don't have a File box yet.")
    with path.open("rb") as stream:
        new_id, _ = store.add_file(conn, settings, store.receive(stream, row["name"], settings), inbox, user["id"])
    return {"file_id": new_id, "url": f"/files#c={inbox}"}


@router.get("/api/rooms/browse")
def browse(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    return {"channels": rooms.browse(conn, user, _dir(request))}


# Before /api/rooms/{room_id}, which would otherwise take "stream" for a room id.
@router.get("/api/rooms/stream")
async def stream(request: Request, after: int = 0, user=Depends(api_user)):
    """Server-sent events for every room this person can see, from event `after` on."""
    bus = _bus(request)
    return StreamingResponse(bus.stream(user, after, _dir(request), request.is_disconnected),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ChannelBody(BaseModel):
    name: str = Field(min_length=1, max_length=41)
    topic: str = Field("", max_length=rooms.MAX_TOPIC)
    visibility: Literal["open", "private", "group"] = "open"
    group: str = Field("", max_length=2000)


@router.post("/api/rooms", status_code=status.HTTP_201_CREATED)
def create_channel(body: ChannelBody, request: Request, user=Depends(api_user),
                   conn: sqlite3.Connection = Depends(get_db)):
    if not rooms.may_create(conn, user, _dir(request), admin=is_admin(request, conn, user)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only some people may make channels here; ask an admin, "
                                                       "or message people directly.")
    try:
        with transaction(conn):
            room_id = rooms.create_channel(conn, user, body.name, topic=body.topic, visibility=body.visibility,
                                           group_ref=body.group)
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    _done(request)
    return {"room": rooms.view_room(conn, rooms.room(conn, room_id), user)}


class DmBody(BaseModel):
    people: list[str] = Field(min_length=1, max_length=rooms.MAX_GROUP_DM)


@router.post("/api/rooms/dm")
def open_dm(body: DmBody, request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            room_id = rooms.open_dm(conn, user, body.people)
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    _done(request)
    return {"room": rooms.view_room(conn, rooms.room(conn, room_id), user)}


@router.get("/api/rooms/{room_id}")
def get_room(room_id: int, request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    admin = is_admin(request, conn, user)
    try:
        r = rooms.visible(conn, user, room_id, _dir(request), admin=admin)
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    return {"room": rooms.view_room(conn, r, user),
            "members": [u["username"] for u in rooms.audience(conn, r, _dir(request))]}


class RoomPatch(BaseModel):
    name: str | None = Field(None, max_length=41)
    topic: str | None = Field(None, max_length=rooms.MAX_TOPIC)
    erynne: bool | None = None
    archived: bool | None = None
    notify: Literal["all", "mentions", "none"] | None = None


@router.patch("/api/rooms/{room_id}")
def patch_room(room_id: int, body: RoomPatch, request: Request, user=Depends(api_user),
               conn: sqlite3.Connection = Depends(get_db)):
    admin = is_admin(request, conn, user)
    try:
        with transaction(conn):
            if body.notify is not None:
                rooms.set_notify(conn, user, room_id, body.notify, _dir(request))
            changes = body.model_dump(exclude_none=True, exclude={"notify"})
            if changes:
                r = rooms.room(conn, room_id)
                owner = conn.execute("SELECT role FROM chat_members WHERE room_id = ? AND user_id = ?",
                                     (room_id, user["id"])).fetchone()
                rooms.update(conn, user, room_id, _dir(request), admin=admin, **changes)
                if admin and r["kind"] == "channel" and not (owner and owner["role"] == "owner"):
                    _log(conn, user, "rooms.moderate", f"#{r['name']}", ", ".join(f"{k}={v}" for k, v in changes.items()))
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    _done(request)
    return {"room": rooms.view_room(conn, rooms.room(conn, room_id), user)}


class MemberBody(BaseModel):
    action: Literal["join", "leave", "invite", "remove"]
    user: str = Field("", max_length=64)


@router.post("/api/rooms/{room_id}/members")
def members(room_id: int, body: MemberBody, request: Request, user=Depends(api_user),
            conn: sqlite3.Connection = Depends(get_db)):
    d = _dir(request)
    try:
        with transaction(conn):
            if body.action == "join":
                rooms.join(conn, user, room_id, d)
            elif body.action == "leave":
                rooms.leave(conn, user, room_id, d)
            elif body.action == "invite":
                rooms.invite(conn, user, room_id, body.user, d)
            else:
                rooms.remove(conn, user, room_id, body.user, d, admin=is_admin(request, conn, user))
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    _done(request)
    return {"ok": True}


@router.post("/api/rooms/{room_id}/files", status_code=status.HTTP_201_CREATED)
async def upload(room_id: int, request: Request, file: UploadFile = File(...), user=Depends(api_user),
                 conn: sqlite3.Connection = Depends(get_db)):
    """Keep a file to attach to the next message in this room."""
    settings = request.app.state.settings
    try:
        out = await anyio.to_thread.run_sync(lambda: rooms.save_upload(conn, settings, user, room_id, file.filename,
                                                                       file.file, _dir(request)))
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    return {"file": out}


class ReadBody(BaseModel):
    message_id: int = Field(ge=0)


@router.post("/api/rooms/{room_id}/read")
def read(room_id: int, body: ReadBody, request: Request, user=Depends(api_user),
         conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            rooms.mark_read(conn, user, room_id, body.message_id, _dir(request))
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    _bus(request).push({user["id"]}, "read", {"room_id": room_id, "message_id": body.message_id})
    return {"ok": True}


@router.post("/api/rooms/{room_id}/typing")
def typing(room_id: int, request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """Says the person is typing to everyone the room reaches (only to open streams; not stored)."""
    if not _limiter(request).allow(f"typing:{user['id']}", TYPING_PER_MINUTE, 60):
        return {"ok": False}
    try:
        r = rooms.visible(conn, user, room_id, _dir(request))
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    bus = _bus(request)
    bus.touch(user["id"])
    # Anyone may be reading an open channel without joining it: tell every stream (the page shows it
    # only in that room). Otherwise just the people the room reaches.
    reach = None if r["visibility"] == "open" else {u["id"] for u in rooms.audience(conn, r, _dir(request))} - {user["id"]}
    bus.push(reach, "typing", {"room_id": room_id, "user": user["username"]})
    return {"ok": True}


# ----------------------------------------------------------------- messages
@router.get("/api/rooms/{room_id}/messages")
def messages(room_id: int, request: Request, before: int | None = None, limit: int = rooms.PAGE,
             user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    try:
        listed = rooms.history(conn, user, room_id, _dir(request), before=before, limit=limit,
                               admin=is_admin(request, conn, user))
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    return {"messages": listed}


class PostBody(BaseModel):
    text: str = Field("", max_length=rooms.MAX_CHARS)
    thread: int | None = None
    attachments: list[int] = Field(default_factory=list, max_length=10)
    bfgguf: bool = False   # @Erynne answers with BFGguf (the experimental group)


@router.post("/api/rooms/{room_id}/messages", status_code=status.HTTP_201_CREATED)
def post_message(room_id: int, body: PostBody, request: Request, user=Depends(api_user),
                 conn: sqlite3.Connection = Depends(get_db)):
    if not _limiter(request).allow(f"post:{user['id']}", POSTS_PER_MINUTE, 60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "That's a lot of messages; wait a moment.")
    bus = _bus(request)
    try:
        with transaction(conn):
            message, asked = rooms.post(conn, user, room_id, body.text, _dir(request), thread_root_id=body.thread,
                                        attachment_ids=body.attachments, online=bus.online())
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    bus.touch(user["id"])
    _done(request)
    erynne = getattr(request.app.state, "rooms_erynne", None)
    if asked and erynne is not None:
        erynne.ask(user["id"], message, bfgguf=body.bfgguf)
    return {"message": message}


@router.get("/api/rooms/messages/{message_id}/thread")
def get_thread(message_id: int, request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    try:
        return {"messages": rooms.thread(conn, user, message_id, _dir(request))}
    except rooms.RoomError as exc:
        raise _refuse(exc) from None


@router.get("/api/rooms/{room_id}/pins")
def get_pins(room_id: int, request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    try:
        return {"messages": rooms.pins(conn, user, room_id, _dir(request))}
    except rooms.RoomError as exc:
        raise _refuse(exc) from None


class EditBody(BaseModel):
    text: str = Field(min_length=1, max_length=rooms.MAX_CHARS)


@router.patch("/api/rooms/messages/{message_id}")
def edit_message(message_id: int, body: EditBody, request: Request, user=Depends(api_user),
                 conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            out = rooms.edit(conn, user, message_id, body.text, _dir(request))
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    _done(request)
    return {"message": out}


@router.delete("/api/rooms/messages/{message_id}")
def delete_message(message_id: int, request: Request, user=Depends(api_user),
                   conn: sqlite3.Connection = Depends(get_db)):
    admin = is_admin(request, conn, user)
    try:
        with transaction(conn):
            row = conn.execute("SELECT user_id, room_id FROM chat_messages WHERE id = ?", (message_id,)).fetchone()
            out = rooms.delete(conn, user, message_id, _dir(request), admin=admin)
            if admin and row and row["user_id"] != user["id"]:
                _log(conn, user, "rooms.delete_message", f"room {row['room_id']}", f"message {message_id}")
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    _done(request)
    return {"message": out}


class ReactBody(BaseModel):
    emoji: str = Field(min_length=1, max_length=16)
    on: bool = True


@router.post("/api/rooms/messages/{message_id}/react")
def react(message_id: int, body: ReactBody, request: Request, user=Depends(api_user),
          conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            out = rooms.react(conn, user, message_id, body.emoji, body.on, _dir(request))
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    _done(request)
    return {"message": out}


class PinBody(BaseModel):
    pinned: bool = True


@router.post("/api/rooms/messages/{message_id}/pin")
def pin(message_id: int, body: PinBody, request: Request, user=Depends(api_user),
        conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            out = rooms.pin(conn, user, message_id, body.pinned, _dir(request))
    except rooms.RoomError as exc:
        raise _refuse(exc) from None
    _done(request)
    return {"message": out}



# ------------------------------------------------------------------- admin
class CreatorsBody(BaseModel):
    group: str | None = Field(None, max_length=2000)   # None: keep
    enabled: bool | None = None                         # None: keep


def _admin_status(request: Request, conn: sqlite3.Connection) -> dict:
    return {"enabled": rooms.enabled(conn), "creators": group_refs.label(conn, rooms.creators(conn)),
            "channels": rooms.all_channels(conn)}


@admin_router.get("/api/admin/settings/rooms")
def admin_rooms(request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return _admin_status(request, conn)


@admin_router.put("/api/admin/settings/rooms")
async def admin_settings(body: CreatorsBody, request: Request, user=Depends(admin_user),
                         conn: sqlite3.Connection = Depends(get_db)):
    """Rooms on or off for this organisation, and who may make channels (a group, or everyone)."""
    name = None
    if body.group is not None:
        directory = _dir(request)
        known = await anyio.to_thread.run_sync(directory.groups) if directory.enabled else []
        try:
            name = group_refs.resolve(conn, body.group, known) if body.group.strip() else ""
        except group_refs.GroupRefError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    with transaction(conn):
        if body.enabled is not None and body.enabled != rooms.enabled(conn):
            rooms.set_enabled(conn, body.enabled)
            _log(conn, user, "settings.rooms", "on" if body.enabled else "off")
        if name is not None:
            rooms.set_creators(conn, name)
            _log(conn, user, "settings.rooms_creators", group_refs.label(conn, name) or "(everyone)")
    if body.enabled is False:
        # Open Rooms tabs: tell them to stop (their streams end on the next look anyway).
        _bus(request).push(None, "end", {"reason": "Rooms was turned off"})
    return _admin_status(request, conn)
