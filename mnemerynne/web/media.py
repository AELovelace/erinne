"""Media: the page, its API, file and session streaming, the radio proxy, casting to screens, and
Settings → Media (mnemerynne/media/).

Every library route takes the signed-in person, or for a paired screen (the TV) its owner
(media_user), and serves only files they can read on the share (media.browse.View). While Media is
off, everything but the admin settings answers 404.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import sqlite3
from typing import Literal
from urllib.parse import quote

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from mnemerynne import group_refs, media, users
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.ingest import store
from mnemerynne.media import artwork, browse, catalog, live, probe, screens, transcode
from mnemerynne.web import security
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import _session_user, admin_user, api_user, get_db, get_settings, is_admin, page_user


def _media_on(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> None:
    """Every Media route but the admin settings: not there at all while Media is turned off, nor for
    anyone outside the Media group (Settings → Media). Without a person or screen, the route's own
    sign-in check answers."""
    if not media.enabled(conn):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    user = _session_user(request, conn)
    if user is None:
        screen = _screen(request, conn)
        user = users.get_user_by_id(conn, screen["user_id"]) if screen is not None else None
    if user is not None and not media.may_use(conn, user, request.app.state.directory):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


def _screen(request: Request, conn: sqlite3.Connection) -> sqlite3.Row | None:
    return screens.by_token(conn, request.cookies.get(screens.COOKIE))


def media_user(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    """The signed-in person, or on a paired screen (the TV) its owner: the screen key opens Media only."""
    user = _session_user(request, conn)
    if user is not None:
        return user
    screen = _screen(request, conn)
    user = users.get_user_by_id(conn, screen["user_id"]) if screen is not None else None
    if user is None or not user["active"] or not screens.may_use(conn, user, request.app.state.directory):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


def screen_device(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    """The screen this browser is (by its key), for the screen's own stream and reports."""
    screen = _screen(request, conn)
    owner = users.get_user_by_id(conn, screen["user_id"]) if screen is not None else None
    if owner is None or not screens.may_use(conn, owner, request.app.state.directory):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This browser isn't a paired screen")
    return screen


def screens_user(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    """The signed-in person, if they are in the screens group (Settings → Media)."""
    if not screens.may_use(conn, user, request.app.state.directory):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Screens are only for members of the group an admin "
                                                       "chose (Settings → Media).")
    return user


router = APIRouter(dependencies=[Depends(_media_on)])
admin_router = APIRouter()   # Settings → Media: reachable when it's off, to turn it on
share_router = APIRouter()   # the Share menu target (manifest): answers even with Media off

# Browsers want these names for <video>/<audio>; mimetypes doesn't know all of them everywhere.
_MIME = {".mkv": "video/x-matroska", ".mka": "audio/x-matroska", ".webm": "video/webm", ".m4v": "video/mp4",
         ".flac": "audio/flac", ".opus": "audio/ogg", ".oga": "audio/ogg", ".m4a": "audio/mp4", ".ts": "video/mp2t",
         ".heic": "image/heic", ".avif": "image/avif", ".webp": "image/webp"}


def sessions(request: Request) -> transcode.Manager:
    """The app's transcoding sessions (made at startup; on first use in tests without one)."""
    if getattr(request.app.state, "media_sessions", None) is None:
        request.app.state.media_sessions = transcode.Manager(request.app.state.settings)
    return request.app.state.media_sessions


def _view(conn: sqlite3.Connection, user) -> browse.View:
    return browse.View(conn, user["id"])


def _not_found(what: str = "Not found") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, what)


# --------------------------------------------------------------------- page
@router.get("/media", response_class=HTMLResponse)
def media_page(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_db)):
    return request.app.state.templates.TemplateResponse(request, "media.html", {
        "user": user, "is_admin": is_admin(request, conn, user), "active": "media",
        "has_library": media.library(conn) is not None, "folder": _folder_name(conn),
        "screens_allowed": screens.may_use(conn, user, request.app.state.directory)})


def _folder_name(conn: sqlite3.Connection) -> str:
    lib = media.library(conn)
    return lib["share_path"] if lib is not None else media.load_settings(conn)["folder"]


# ------------------------------------------------------------------ library
@router.get("/api/media/home")
def home(user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db)):
    view = _view(conn, user)
    return {"library": view.lib is not None and view.lib["id"] in view.access.readable, **browse.home(view)}


@router.get("/api/media/movies")
def movies(q: str = "", sort: Literal["title", "year", "added"] = "title", user=Depends(media_user),
           conn: sqlite3.Connection = Depends(get_db)):
    return {"movies": browse.movies(_view(conn, user), q, sort)}


@router.get("/api/media/items/{file_id}")
def media_item(file_id: int, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db)):
    found = browse.item(_view(conn, user), file_id)
    if found is None:
        raise _not_found()
    return found


@router.get("/api/media/music/artists")
def artists(user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db)):
    return {"artists": browse.artists(_view(conn, user))}


@router.get("/api/media/music/albums")
def albums(artist: str = "", user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db)):
    return {"albums": browse.albums(_view(conn, user), artist)}


@router.get("/api/media/music/album")
def album(key: str, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db)):
    found = browse.album(_view(conn, user), key)
    if found is None:
        raise _not_found()
    return found


@router.get("/api/media/pictures")
def pictures(folder: int | None = None, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db)):
    found = browse.pictures(_view(conn, user), folder)
    if found is None:
        raise _not_found()
    return found


@router.get("/media/art/{artwork_id}")
def art(artwork_id: int, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db),
        settings: Settings = Depends(get_settings)):
    row = conn.execute("SELECT rel_path FROM media_artwork WHERE id = ?", (artwork_id,)).fetchone()
    if row is None or not browse.artwork_readable(_view(conn, user), artwork_id):
        raise _not_found()
    path = artwork.path_of(settings, row["rel_path"])
    if not path.is_file():
        raise _not_found()
    return FileResponse(path, media_type="image/jpeg", headers={
        "Cache-Control": "private, max-age=604800, immutable", "X-Content-Type-Options": "nosniff"})


def _readable_item(conn: sqlite3.Connection, user, file_id: int) -> sqlite3.Row:
    """The catalog row of a file this person can read, with the file's name; 404 otherwise."""
    view = _view(conn, user)
    rows = view.rows("mi.file_id = ?", (file_id,))
    if not rows:
        raise _not_found("File not found")
    return rows[0]


@router.get("/media/files/{file_id}/stream")
def stream_file(file_id: int, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    """The file itself, inline, with Range (seeking) support: for what the browser plays as it is."""
    row = _readable_item(conn, user, file_id)
    path = store.content_path(conn, settings, file_id)
    if not path.is_file():
        raise _not_found("File not found")
    name = row["display_name"]
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    mime = _MIME.get(suffix) or mimetypes.guess_type(name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=mime, filename=name, content_disposition_type="inline", headers={
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "private, max-age=3600"})


# --------------------------------------------------------------------- play
class Caps(BaseModel):
    hevc: bool = False
    ac3: bool = False
    eac3: bool = False
    alac: bool = False


class PlayBody(BaseModel):
    start: float = Field(0.0, ge=0)
    slot: Literal["video", "audio"] = "video"
    caps: Caps = Caps()


@router.post("/api/media/play/{file_id}")
async def play(file_id: int, body: PlayBody, request: Request, user=Depends(media_user),
               conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """How to play a file in this browser: the file itself ('direct'), or an HLS session from the start
    position (remux, audio, transcode) once its first segment is ready."""
    row = _readable_item(conn, user, file_id)
    mode = probe.decide(row["media_type"], row["container"], row["video_codec"], row["audio_codec"],
                        body.caps.model_dump())
    base = {"id": file_id, "duration": row["duration_s"], "title": row["title"]}
    if mode == "direct":
        return {**base, "mode": "direct", "url": f"/media/files/{file_id}/stream", "start": body.start}
    path = store.content_path(conn, settings, file_id)
    if not path.is_file():
        raise _not_found("File not found")
    cfg = media.load_settings(conn)
    manager = sessions(request)

    def start():
        session = manager.start_file(user_id=user["id"], slot=body.slot, file_id=file_id, path=path,
                                     kind=row["media_type"], video_codec=row["video_codec"],
                                     audio_codec=row["audio_codec"], mode=mode, start=body.start,
                                     hwaccel=cfg["hwaccel"], max_transcodes=int(cfg["max_transcodes"]))
        manager.wait_ready(session)
        return session

    try:
        session = await anyio.to_thread.run_sync(start)
    except transcode.TranscodeError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return {**base, "mode": mode, "url": f"/media/sessions/{session.id}/index.m3u8", "session": session.id,
            "start": body.start, "backend": session.backend}


@router.get("/media/sessions/{sid}/{name}")
def session_file(sid: str, name: str, request: Request, user=Depends(media_user)):
    session = sessions(request).get(sid)
    if session is None or (session.user_id is not None and session.user_id != user["id"]):
        raise _not_found()
    path = sessions(request).file(session, name)
    if path is None:
        raise _not_found()
    return FileResponse(path, media_type=transcode.media_type_of(name), headers={
        "Cache-Control": "no-cache" if name.endswith(".m3u8") else "private, max-age=3600",
        "X-Content-Type-Options": "nosniff"})


@router.delete("/api/media/sessions/{sid}")
def stop_session(sid: str, request: Request, user=Depends(media_user)):
    """The player closed: stop its ffmpeg now rather than when it goes idle. Shared live sessions are
    left to idle out, since others may be watching."""
    sessions(request).stop(sid, user_id=user["id"])
    return {"status": "stopped"}


class ProgressBody(BaseModel):
    position: float = Field(..., ge=0)
    duration: float | None = Field(None, ge=0)


@router.put("/api/media/progress/{file_id}")
def progress(file_id: int, body: ProgressBody, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db)):
    _readable_item(conn, user, file_id)
    with transaction(conn):
        browse.save_progress(conn, user["id"], file_id, body.position, body.duration)
    return {"status": "saved"}


# --------------------------------------------------------------------- live
@router.get("/api/media/live")
def live_list(user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db)):
    stations, problem = live.stations(conn)
    streams = [{"id": s["id"], "kind": s["kind"], "name": s["name"]}
               | ({"embed": s["url"], "what": live.youtube_what(s["url"])} if s["kind"] == "youtube" else {})
               for s in live.sources(conn) if s["kind"] in ("rtmp", "hls", "youtube")]
    return {"streams": streams, "stations": stations, "problem": problem}


@router.post("/api/media/live/{source_id}/watch")
async def watch(source_id: int, request: Request, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db)):
    src = live.source(conn, source_id)
    if src is None or not src["enabled"] or src["kind"] not in ("rtmp", "hls"):
        raise _not_found()
    manager = sessions(request)

    def start():
        session = manager.watch_live(source_id, src["url"])
        manager.wait_ready(session)
        return session

    try:
        session = await anyio.to_thread.run_sync(start)
    except transcode.TranscodeError as exc:
        raise HTTPException(exc.status, f"{src['name']}: {exc}") from None
    return {"mode": "live", "url": f"/media/sessions/{session.id}/index.m3u8", "session": session.id,
            "title": src["name"]}


@router.get("/media/radio/{key}/stream")
async def radio_stream(key: str, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db)):
    """A plain-http station, relayed so a page served over https may play it."""
    found = await anyio.to_thread.run_sync(live.station, conn, key)
    if found is None:
        raise _not_found()
    client = httpx.AsyncClient(transport=live.TRANSPORT, timeout=httpx.Timeout(10.0, read=60.0),
                               follow_redirects=True)
    upstream = None
    try:
        upstream = await client.send(client.build_request("GET", found["url"], headers={"Icy-MetaData": "0"}),
                                     stream=True)
        upstream.raise_for_status()
    except httpx.HTTPError as exc:
        if upstream is not None:
            await upstream.aclose()
        await client.aclose()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"{found['name']} isn't answering ({type(exc).__name__}).")

    async def body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(body(), media_type=upstream.headers.get("content-type", "audio/mpeg"),
                             headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})


# ------------------------------------------------------------------- screens
def hub(request: Request) -> screens.Hub:
    if getattr(request.app.state, "media_screens", None) is None:
        request.app.state.media_screens = screens.Hub()
    return request.app.state.media_screens


def _key_cookie(response, request: Request, token: str) -> None:
    response.set_cookie(screens.COOKIE, token, max_age=screens.COOKIE_DAYS * 86400, httponly=True, samesite="lax",
                        secure=security.https_only(request.app.state.settings), path="/")


@router.get("/media/screen", response_class=HTMLResponse)
def screen_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """The TV: Media full-screen, waiting for what its owner casts. Unpaired browsers go to pairing."""
    screen = _screen(request, conn)
    owner = users.get_user_by_id(conn, screen["user_id"]) if screen is not None else None
    if owner is None or not screens.may_use(conn, owner, request.app.state.directory):
        return RedirectResponse("/media#screens", status_code=status.HTTP_303_SEE_OTHER)
    return request.app.state.templates.TemplateResponse(request, "media.html", {
        "user": owner, "is_admin": False, "active": "media", "has_library": media.library(conn) is not None,
        "folder": _folder_name(conn), "screen": {"id": screen["id"], "name": screen["name"]}})


class ScreenBody(BaseModel):
    name: str = Field(..., max_length=200)


@router.post("/api/media/screens", status_code=status.HTTP_201_CREATED)
def pair_screen(body: ScreenBody, request: Request, user=Depends(screens_user),
                conn: sqlite3.Connection = Depends(get_db)):
    """Make this browser one of the person's screens: it gets the screen key as a cookie."""
    try:
        with transaction(conn):
            screen_id, token = screens.create(conn, user["id"], body.name)
    except screens.ScreenError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    response = JSONResponse({"id": screen_id, "name": screens.clean_name(body.name), "url": "/media/screen"},
                            status_code=status.HTTP_201_CREATED)
    _key_cookie(response, request, token)
    return response


@router.get("/api/media/screens")
def list_screens(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    h = hub(request)
    mine = _screen(request, conn)
    # Out of the group: only their screens, to remove them (and casting refused).
    return {"allowed": screens.may_use(conn, user, request.app.state.directory), "screens": [{"id": s["id"], "name": s["name"], "online": h.online(s["id"]), "state": h.state(s["id"]),
                         "last_seen": s["last_seen_at"], "this": mine is not None and mine["id"] == s["id"]}
                        for s in screens.of_user(conn, user["id"])]}


@router.delete("/api/media/screens/{screen_id}")
async def remove_screen(screen_id: int, request: Request, user=Depends(api_user),
                        conn: sqlite3.Connection = Depends(get_db)):
    if screens.owned(conn, user["id"], screen_id) is None:
        raise _not_found()
    with transaction(conn):
        conn.execute("DELETE FROM media_screens WHERE id = ?", (screen_id,))
    hub(request).close(screen_id)
    return {"status": "removed"}


class Command(BaseModel):
    action: Literal["play_item", "play_album", "play_youtube", "play_source", "play_station", "toggle", "seek",
                    "skip", "volume", "next", "prev", "stop"]
    file_id: int | None = None
    key: str | None = Field(None, max_length=2000)       # album key or station key
    index: int = Field(0, ge=0)
    shuffle: bool = False
    url: str | None = Field(None, max_length=1000)       # play_youtube
    source_id: int | None = None
    start: float = Field(0.0, ge=0)
    to: float | None = Field(None, ge=0)                 # seek
    by: float | None = Field(None, ge=-36000, le=36000)  # skip
    level: float | None = Field(None, ge=0, le=1)        # volume


def _command(conn: sqlite3.Connection, user, body: Command) -> dict:
    """What the screen is sent: plays carry what to play, checked against what the person may read."""
    view = _view(conn, user)
    if body.action == "play_item":
        found = browse.item(view, body.file_id or 0)
        if found is None:
            raise _not_found("File not found")
        return {"action": "play_item", "item": found, "start": body.start}
    if body.action == "play_album":
        found = browse.album(view, body.key or "")
        if found is None or body.index >= len(found["tracks"]):
            raise _not_found("Album not found")
        return {"action": "play_album", "album": found, "index": body.index, "shuffle": body.shuffle}
    if body.action == "play_youtube":
        try:
            embed = live.youtube_embed(body.url or "")
        except live.LiveError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        return {"action": "play_youtube",
                "source": {"name": "YouTube", "embed": embed, "what": live.youtube_what(embed)}}
    if body.action == "play_source":
        src = live.source(conn, body.source_id or 0)
        if src is None or not src["enabled"] or src["kind"] == "radio":
            raise _not_found()
        source = {"id": src["id"], "name": src["name"], "kind": src["kind"]}
        if src["kind"] == "youtube":
            source.update(embed=src["url"], what=live.youtube_what(src["url"]))
        return {"action": "play_source", "source": source}
    if body.action == "play_station":
        found = live.station(conn, body.key or "")
        if found is None:
            raise _not_found()
        return {"action": "play_station", "station": found}
    return {k: v for k, v in body.model_dump().items() if k in ("action", "to", "by", "level") and v is not None}


@router.post("/api/media/screens/{screen_id}/command")
async def cast(screen_id: int, body: Command, request: Request, user=Depends(screens_user),
               conn: sqlite3.Connection = Depends(get_db)):
    """Play something on, or control, one of the person's screens."""
    screen = screens.owned(conn, user["id"], screen_id)
    if screen is None:
        raise _not_found()
    command = await anyio.to_thread.run_sync(_command, conn, user, body)
    if not hub(request).push(screen_id, command):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{screen['name']} isn't connected: is Media open on it?")
    return {"status": "sent"}


@router.get("/media/screen/stream")
async def screen_stream(request: Request, screen=Depends(screen_device), conn: sqlite3.Connection = Depends(get_db)):
    """The screen's commands, as server-sent events, with a comment every 20 s so dead connections close."""
    with transaction(conn):
        screens.seen(conn, screen["id"])
    h = hub(request)
    queue = h.listen(screen["id"])

    async def events():
        try:
            yield f"retry: 3000\nevent: hello\ndata: {json.dumps({'name': screen['name']})}\n\n"
            while True:
                try:
                    command = await asyncio.wait_for(queue.get(), 20)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"event: cmd\ndata: {json.dumps(command)}\n\n"
                if command.get("action") == "unpaired":
                    return
        finally:
            h.unlisten(screen["id"], queue)

    response = StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    # The key is renewed whenever the screen connects, so a TV in daily use never loses it.
    _key_cookie(response, request, request.cookies[screens.COOKIE])
    return response


class ScreenState(BaseModel):
    kind: Literal["idle", "video", "live", "youtube", "audio", "radio", "pictures"]
    title: str = Field("", max_length=300)
    sub: str = Field("", max_length=300)
    art: str | None = Field(None, max_length=300)
    position: float | None = None
    duration: float | None = None
    paused: bool = False
    volume: float | None = Field(None, ge=0, le=1)
    has_queue: bool = False


@router.put("/api/media/screen/state")
async def screen_state(body: ScreenState, request: Request, screen=Depends(screen_device)):
    """What the screen is playing, every few seconds and on every change; read by its owner's remote."""
    hub(request).set_state(screen["id"], body.model_dump())
    return {"status": "ok"}

# -------------------------------------------------------------------- admin
def _admin_status(request: Request, conn: sqlite3.Connection) -> dict:
    cfg = media.load_settings(conn)
    lib = media.library(conn)
    return {"enabled": media.enabled(conn),
            "library": {"id": lib["id"], "name": lib["name"], "share_path": lib["share_path"]} if lib else None,
            "settings": {k: cfg[k] for k in media.DEFAULTS}
                        | {g: group_refs.label(conn, cfg[g]) for g in media.GROUP_SETTINGS},
            "tmdb_key_set": bool(cfg.get("tmdb_key_enc")),
            "sources": live.sources(conn, enabled_only=False), "catalog": catalog.status(conn, lib),
            "sessions": sessions(request).running(),
            # Top-level folders of the share, to pick the Media folder from (inboxes aren't libraries).
            "folders": [r["share_path"] for r in conn.execute(
                "SELECT share_path FROM collections WHERE kind = 'library' AND share_path IS NOT NULL "
                "AND instr(share_path, '/') = 0 ORDER BY share_path COLLATE NOCASE")]}


@admin_router.get("/api/admin/settings/media")
def admin_media(request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return _admin_status(request, conn)


class MediaSettingsBody(BaseModel):
    enabled: bool | None = None
    folder: str | None = Field(None, max_length=120)      # the Media folder at the top of the share
    media_group: str | None = Field(None, max_length=2000)     # who may use Media; "": nobody
    screens_group: str | None = Field(None, max_length=2000)   # who may use screens; "": nobody
    hwaccel: Literal["auto", "vaapi", "nvenc", "software"] | None = None
    max_transcodes: int | None = Field(None, ge=0, le=8)
    online_metadata: bool | None = None
    radio_directory_url: str | None = Field(None, max_length=1000)
    tmdb_key: str | None = Field(None, max_length=1000)   # None: keep; "": remove


@admin_router.put("/api/admin/settings/media")
def admin_settings(body: MediaSettingsBody, request: Request, user=Depends(admin_user),
                   conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Media on or off (on: the Media folder, with Music, Movies and Pictures, made on the share), which
    folder it is, and its settings."""
    try:
        with transaction(conn):
            if body.folder is not None and media.set_folder(conn, settings, body.folder):
                _log(conn, user, "settings.media_folder", media.check_folder(body.folder))
                if media.enabled(conn) and body.enabled is not False:
                    media.ensure_library(conn)   # the new folder, and its Music, Movies and Pictures
            if body.enabled is True:
                lib = media.ensure_library(conn)
                if not media.enabled(conn):
                    media.set_enabled(conn, True)
                    _log(conn, user, "settings.media", "on", lib["share_path"])
            elif body.enabled is False and media.enabled(conn):
                media.set_enabled(conn, False)
                _log(conn, user, "settings.media", "off")
            changes = body.model_dump(exclude={"enabled", "folder", "tmdb_key"}, exclude_none=True)
            directory = request.app.state.directory
            for name in media.GROUP_SETTINGS:
                if changes.get(name) is not None:
                    known = directory.groups() if directory.enabled else []
                    try:
                        changes[name] = group_refs.resolve(conn, changes[name], known)
                    except group_refs.GroupRefError as exc:
                        raise media.MediaError(str(exc)) from None
            if changes or body.tmdb_key is not None:
                media.save_settings(conn, settings, changes, body.tmdb_key)
                detail = ", ".join(f"{k}={v}" for k, v in changes.items())
                if body.tmdb_key is not None:
                    detail = (detail + ", " if detail else "") + ("TMDB key set" if body.tmdb_key else "TMDB key removed")
                _log(conn, user, "settings.media_options", detail)
    except (media.MediaError, OSError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    if body.enabled is False:
        sessions(request).stop_all()
    return _admin_status(request, conn)


class RescanBody(BaseModel):
    online: bool = False


@admin_router.post("/api/admin/media/rescan")
def admin_rescan(body: RescanBody, request: Request, user=Depends(admin_user),
                 conn: sqlite3.Connection = Depends(get_db)):
    with transaction(conn):
        n = catalog.reset(conn, body.online)
        _log(conn, user, "media.rescan", f"{n} items", "with online lookups" if body.online else "")
    return {"queued": n}


@admin_router.post("/api/admin/media/hwtest")
async def admin_hwtest(request: Request, user=Depends(admin_user)):
    """A two-second test encode with each backend, on this server."""
    return {"backends": await anyio.to_thread.run_sync(sessions(request).test_all)}


class SourceBody(BaseModel):
    kind: Literal["rtmp", "hls", "radio", "youtube"]
    name: str = Field(..., max_length=200)
    url: str = Field(..., max_length=1000)
    enabled: bool = True


@admin_router.post("/api/admin/media/sources", status_code=status.HTTP_201_CREATED)
def add_source(body: SourceBody, request: Request, user=Depends(admin_user),
               conn: sqlite3.Connection = Depends(get_db)):
    try:
        kind, name, url = live.check_source(body.kind, body.name, body.url)
    except live.LiveError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    with transaction(conn):
        sort = conn.execute("SELECT COALESCE(max(sort), 0) + 1 FROM media_sources").fetchone()[0]
        source_id = conn.execute("INSERT INTO media_sources(kind, name, url, enabled, sort, created_by) "
                                 "VALUES (?, ?, ?, ?, ?, ?)",
                                 (kind, name, url, int(body.enabled), sort, user["id"])).lastrowid
        _log(conn, user, "media.source_add", name, kind)
    return _admin_status(request, conn) | {"id": source_id}


@admin_router.put("/api/admin/media/sources/{source_id}")
def edit_source(source_id: int, body: SourceBody, request: Request, user=Depends(admin_user),
                conn: sqlite3.Connection = Depends(get_db)):
    if live.source(conn, source_id) is None:
        raise _not_found()
    try:
        kind, name, url = live.check_source(body.kind, body.name, body.url)
    except live.LiveError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    with transaction(conn):
        conn.execute("UPDATE media_sources SET kind = ?, name = ?, url = ?, enabled = ? WHERE id = ?",
                     (kind, name, url, int(body.enabled), source_id))
        _log(conn, user, "media.source_edit", name, kind)
    sessions(request).stop_live(source_id)   # viewers reconnect to the new address
    return _admin_status(request, conn)


@admin_router.delete("/api/admin/media/sources/{source_id}")
def delete_source(source_id: int, request: Request, user=Depends(admin_user),
                  conn: sqlite3.Connection = Depends(get_db)):
    src = live.source(conn, source_id)
    if src is None:
        raise _not_found()
    with transaction(conn):
        conn.execute("DELETE FROM media_sources WHERE id = ?", (source_id,))
        _log(conn, user, "media.source_delete", src["name"], src["kind"])
    sessions(request).stop_live(source_id)
    return _admin_status(request, conn)


# --------------------------------------------------------------- share menu
_LINK = re.compile(r"https?://[^\s<>\"']+")


def shared_link(*fields: str | None) -> str | None:
    """The link in what an app shared: YouTube puts it in `text` (sometimes after the title), others in `url`."""
    for value in fields:
        match = _LINK.search(value or "")
        if match:
            return match.group(0).rstrip(".,;)")
    return None


@share_router.get("/media/share")
def share_link(request: Request, title: str = "", text: str = "", url: str = "",
               conn: sqlite3.Connection = Depends(get_db)):
    """Android's Share → Erynne (the installed app): open Media → Screens with the link filled in, one tap
    from playing on the TV. Signed out, it waits for sign-in and comes back here."""
    if _session_user(request, conn) is None:
        request.session["after_login"] = str(request.url.path) + "?" + str(request.url.query)
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not media.enabled(conn) or not media.may_use(conn, _session_user(request, conn), request.app.state.directory):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    link = shared_link(url, text, title)
    target = "/media#cast/" + quote(link, safe="") if link else "/media#screens"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)

