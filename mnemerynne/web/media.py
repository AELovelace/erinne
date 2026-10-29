"""Media: the page, its API, file and session streaming, the radio proxy, casting to screens, and
Settings → Entertainment → Media (mnemerynne/media/).

Every library route takes the signed-in person, or for a paired screen (the TV) its owner
(media_user), and serves only files they can read on the share (media.browse.View). While Media is
off, everything but the admin settings answers 404.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import random
import re
import sqlite3
from typing import Literal
from urllib.parse import quote

import anyio
import httpx
import segno
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from mnemerynne import group_refs, media, users
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.ingest import store
from mnemerynne.media import artwork, browse, catalog, live, probe, remotes, screens, signage, speakers, transcode
from mnemerynne.web import security
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import _session_user, admin_user, api_user, get_db, get_settings, is_admin, page_user

log = logging.getLogger("mnemerynne.media")


def _media_on(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> None:
    """Every Media route but the admin settings: not there at all while Media is turned off, nor for
    anyone outside the Media group (Settings → Entertainment → Media). Without a person or screen, the route's own
    sign-in check answers."""
    if not media.enabled(conn):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    user = _session_user(request, conn)
    # The TV's own routes go by its screen key, even while someone is signed in on it (its top bar).
    if user is None or request.url.path.startswith(("/media/screen", "/api/media/screen/")):
        screen = _screen(request, conn)
        if screen is not None:
            user = users.get_user_by_id(conn, screen["user_id"])
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
    """The signed-in person, if they are in the screens group (Settings → Entertainment → Media)."""
    if not screens.may_use(conn, user, request.app.state.directory):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Screens are only for members of the group an admin "
                                                       "chose (Settings → Entertainment → Media).")
    return user


router = APIRouter(dependencies=[Depends(_media_on)])
admin_router = APIRouter()   # Settings → Entertainment → Media: reachable when it's off, to turn it on
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


@router.get("/api/media/tv")
def tv_shows(user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db)):
    """The shows in Media/TV (each folder directly in it), for the TV Shows tab."""
    return {"shows": browse.tv_shows(_view(conn, user))}


@router.get("/api/media/tv/{folder_id}")
def tv_show(folder_id: int, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db)):
    """One show: its seasons and episodes, the order they play in, and the episode to watch next."""
    found = browse.tv_show(_view(conn, user), folder_id)
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


def pairings(request: Request) -> screens.Pairings:
    if getattr(request.app.state, "media_pairings", None) is None:
        request.app.state.media_pairings = screens.Pairings()
    return request.app.state.media_pairings


def speaker_tickets(request: Request) -> speakers.Tickets:
    if getattr(request.app.state, "media_speaker_tickets", None) is None:
        request.app.state.media_speaker_tickets = speakers.Tickets()
    return request.app.state.media_speaker_tickets


def _key_cookie(response, request: Request, token: str) -> None:
    response.set_cookie(screens.COOKIE, token, max_age=screens.COOKIE_DAYS * 86400, httponly=True, samesite="lax",
                        secure=security.https_only(request.app.state.settings), path="/")


@router.get("/media/screen", response_class=HTMLResponse)
def screen_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """The TV: Media full-screen, waiting for what its owner casts. Unpaired, it shows a code to pair with
    from a phone (no sign-in needed on the TV). The screen box's start page comes here as ?kiosk=1, which
    marks the screen as a box (the small bar and the remote on every page)."""
    screen = _screen(request, conn)
    owner = users.get_user_by_id(conn, screen["user_id"]) if screen is not None else None
    if owner is None or not screens.may_use(conn, owner, request.app.state.directory):
        return request.app.state.templates.TemplateResponse(request, "media_pair.html", {})
    if request.session.get("kiosk_screen"):
        request.session.clear()      # someone signed in on the TV (its top bar): back on the TV, signed out
    box = bool(screen["is_box"])
    if request.query_params.get("kiosk") == "1" and not box:
        with transaction(conn):
            screens.mark_box(conn, screen["id"])
        box = True
    return request.app.state.templates.TemplateResponse(request, "media.html", {
        "user": owner, "is_admin": False, "active": "media", "has_library": media.library(conn) is not None,
        "folder": _folder_name(conn), "screen": {"id": screen["id"], "name": screen["name"],
                                                 "safe_x": screen["safe_x"], "safe_y": screen["safe_y"],
                                                 "top_bar": bool(screen["top_bar"]), "box": box}})


class ScreenBody(BaseModel):
    name: str = Field(..., max_length=200)


@router.post("/api/media/screens", status_code=status.HTTP_201_CREATED)
async def pair_screen(body: ScreenBody, request: Request, user=Depends(screens_user),
                      conn: sqlite3.Connection = Depends(get_db)):
    """Make this browser one of the person's screens: it gets the screen key as a cookie."""
    try:
        with transaction(conn):
            screen_id, token = screens.create(conn, user["id"], body.name)
    except screens.ScreenError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    hub(request).close(screen_id)       # re-paired: a TV still on the old key goes back to its code
    response = JSONResponse({"id": screen_id, "name": screens.clean_name(body.name), "url": "/media/screen"},
                            status_code=status.HTTP_201_CREATED)
    _key_cookie(response, request, token)
    return response


def _pairing_url(request: Request, code: str) -> str:
    base = request.app.state.settings.public_url.rstrip("/") or str(request.base_url).rstrip("/")
    return f"{base}/media/pair?code={code}"


@router.post("/media/screen/pair")
async def start_pairing(request: Request):
    """An unpaired TV's code (the one it already has while that lasts), with a QR code of where to claim it."""
    p = pairings(request)
    secret = request.cookies.get(screens.PAIR_COOKIE)
    found = p.waiting(secret)
    if found is None:
        try:
            code, secret = p.start()
        except screens.ScreenError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        found = code, float(screens.PAIR_SECONDS)
    code, left = found
    url = _pairing_url(request, code)
    response = JSONResponse({"code": screens.show_code(code), "expires_in": int(left), "url": url,
                             "qr": segno.make(url, error="m").svg_inline(scale=1, border=4, omitsize=True)})
    response.set_cookie(screens.PAIR_COOKIE, secret, max_age=screens.PAIR_SECONDS, httponly=True, samesite="lax",
                        secure=security.https_only(request.app.state.settings), path="/media/screen")
    return response


@router.get("/media/screen/pair")
async def pairing_status(request: Request):
    """What the TV asks every few seconds: still waiting, paired (it gets its key), or the code ran out."""
    secret = request.cookies.get(screens.PAIR_COOKIE)
    p = pairings(request)
    collected = p.collect(secret)
    if collected is not None:
        response = JSONResponse({"state": "paired", "url": "/media/screen"})
        _key_cookie(response, request, collected[1])
        response.delete_cookie(screens.PAIR_COOKIE, path="/media/screen")
        return response
    found = p.waiting(secret)
    if found is None:
        return {"state": "expired"}
    return {"state": "waiting", "expires_in": int(found[1])}


@router.get("/media/pair")
def pair_link(request: Request, code: str = "", conn: sqlite3.Connection = Depends(get_db)):
    """The TV's QR code: Media → Screens on the phone, with the code filled in. Signed out, it waits for
    sign-in and comes back here."""
    if _session_user(request, conn) is None:
        request.session["after_login"] = str(request.url.path) + "?" + str(request.url.query)
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/media#pair/" + quote(screens.clean_code(code), safe=""),
                            status_code=status.HTTP_303_SEE_OTHER)


class ClaimBody(BaseModel):
    code: str = Field(..., max_length=40)
    name: str = Field(..., max_length=200)


@router.post("/api/media/screens/claim", status_code=status.HTTP_201_CREATED)
async def claim_screen(body: ClaimBody, request: Request, user=Depends(screens_user),
                       conn: sqlite3.Connection = Depends(get_db)):
    """Pair the TV (or remote) showing this code as one of the person's screens (or remotes); it collects
    its key itself."""
    p = pairings(request)
    try:
        name = screens.clean_name(body.name)
        code = p.check(user["id"], body.code)
        kind = p.kind(code)
        with transaction(conn):
            if kind == "screen":
                made_id, token = screens.create(conn, user["id"], name)
            else:                                    # a remote, or a remote that is also a speaker
                made_id, token = remotes.create(conn, user["id"], name, speaker=kind == "speaker")
    except screens.ScreenError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    p.claim(code, made_id, token)
    if kind == "screen":
        hub(request).close(made_id)
    return {"id": made_id, "name": name, "kind": kind}


@router.get("/api/media/screens")
def list_screens(request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    h = hub(request)
    mine = _screen(request, conn)
    allowed = screens.may_use(conn, user, request.app.state.directory)
    admin = allowed and is_admin(request, conn, user)

    def row(s: sqlite3.Row, owner: str | None) -> dict:
        return {"id": s["id"], "name": s["name"], "online": h.online(s["id"]), "state": h.state(s["id"]),
                "last_seen": s["last_seen_at"], "this": mine is not None and mine["id"] == s["id"],
                "safe": {"x": s["safe_x"], "y": s["safe_y"]}, "owner": owner, "speaker": bool(s["speaker"])}
    # Out of the group: only their screens, to remove them (and casting refused). Admins: everyone's too.
    out = [row(s, None) for s in screens.of_user(conn, user["id"])]
    if admin:
        out += [row(s, s["owner"]) for s in screens.of_others(conn, user["id"])]
    mine_remotes = [{"id": r["id"], "name": r["name"], "last_seen": r["last_seen_at"]}
                    for r in remotes.of_user(conn, user["id"])]
    return {"allowed": allowed, "admin": admin, "screens": out, "remotes": mine_remotes}


@router.delete("/api/media/screens/{screen_id}")
async def remove_screen(screen_id: int, request: Request, user=Depends(api_user),
                        conn: sqlite3.Connection = Depends(get_db)):
    if screens.owned(conn, user["id"], screen_id) is None:
        raise _not_found()
    with transaction(conn):
        conn.execute("DELETE FROM media_screens WHERE id = ?", (screen_id,))
    hub(request).close(screen_id)
    speaker_tickets(request).forget(screen_id)    # a speaker: its tickets stop at once (its remote stays)
    return {"status": "removed"}


class Command(BaseModel):
    action: Literal["play_item", "play_album", "play_youtube", "play_source", "play_station", "toggle", "seek",
                    "skip", "volume", "mute", "night", "next", "prev", "stop", "press", "calibrate"]
    file_id: int | None = None
    key: str | None = Field(None, max_length=2000)       # album key or station key
    index: int = Field(0, ge=0)
    shuffle: bool = False
    url: str | None = Field(None, max_length=1000)       # play_youtube
    source_id: int | None = None
    start: float = Field(0.0, ge=0)
    to: float | None = Field(None, ge=0)                 # seek
    by: float | None = Field(None, ge=-36000, le=36000)  # skip
    level: float | None = Field(None, ge=0, le=1)        # volume; night (none: the next step)
    step: Literal["open", "wider", "narrower", "taller", "shorter", "reset", "save", "cancel"] | None = None  # calibrate
    button: Literal[remotes.BUTTONS] | None = None       # press
    override: bool = False                              # an admin: no countdown for someone using the TV


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
    if body.action == "calibrate":
        return {"action": "calibrate", "step": body.step or "open"}
    if body.action == "press" and body.button is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Which button?")
    return {k: v for k, v in body.model_dump().items() if k in ("action", "to", "by", "level", "button") and v is not None}


def _castable(request: Request, conn: sqlite3.Connection, user, screen_id: int) -> sqlite3.Row:
    """One of the person's screens; for an admin, anyone's."""
    screen = screens.owned(conn, user["id"], screen_id)
    if screen is None and is_admin(request, conn, user):
        screen = conn.execute("SELECT * FROM media_screens WHERE id = ?", (screen_id,)).fetchone()
    if screen is None:
        raise _not_found()
    return screen


def _for_speaker(request: Request, conn: sqlite3.Connection, user, screen: sqlite3.Row, command: dict) -> dict:
    """A command as a speaker (media/speakers.py) takes it: tracks become a queue of tickets, a station its
    own (public) address, and what it can't play is refused. `user` cast it: the tickets carry their access."""
    action = command["action"]
    if action in ("play_item", "play_album"):
        if action == "play_item":
            if command["item"]["type"] != "audio":
                raise HTTPException(speakers.SpeakerError.status, f"{screen['name']} is a speaker: it plays music and radio only.")
            tracks, index = [command["item"]], 0
        else:
            tracks, index = list(command["album"]["tracks"]), command["index"]
            if command.get("shuffle"):
                random.shuffle(tracks)
                index = 0
        if len(tracks) > speakers.MAX_QUEUE:                  # a huge list: from the chosen track on
            tracks, index = tracks[index:index + speakers.MAX_QUEUE], 0
        tickets = speaker_tickets(request)
        queue = []
        for track in tracks:
            row = _readable_item(conn, user, track["id"])
            ext, convert = speakers.plan(row["container"], row["audio_codec"])
            queue.append({"title": track["title"] or row["display_name"], "duration": track.get("duration"),
                          "sub": " - ".join(x for x in (track.get("artist"), track.get("album")) if x),
                          "ticket": tickets.issue(screen["id"], user["id"], track["id"], ext, convert)})
        return {"action": "play_queue", "tracks": queue, "index": min(index, len(queue) - 1)}
    if action == "play_station":
        station = command["station"]
        return {"action": "play_station", "station": {"name": station["name"], "url": station["url"]}}
    if action in ("play_source", "play_youtube", "calibrate"):
        raise HTTPException(speakers.SpeakerError.status, f"{screen['name']} is a speaker: it plays music and radio only.")
    return command


@router.post("/api/media/screens/{screen_id}/command")
async def cast(screen_id: int, body: Command, request: Request, user=Depends(screens_user),
               conn: sqlite3.Connection = Depends(get_db)):
    """Play something on, or control, one of the person's screens (an admin: anyone's). Someone using the
    TV gets screens.TAKEOVER_SECONDS to keep it, unless an admin overrides; ask after it by its id."""
    screen = _castable(request, conn, user, screen_id)
    if body.override and not is_admin(request, conn, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can override someone using the TV.")
    command = await anyio.to_thread.run_sync(_command, conn, user, body)
    if screen["speaker"]:
        command = await anyio.to_thread.run_sync(_for_speaker, request, conn, user, screen, command)
    command.update(sender=user["username"], override=body.override)
    command_id = hub(request).send(screen_id, command)
    if command_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{screen['name']} isn't connected: is Media open on it?")
    if body.override or screen["user_id"] != user["id"]:
        with transaction(conn):
            _log(conn, user, "screen.cast", screen["name"], body.action + (" (override)" if body.override else ""))
    return {"status": "sent", "id": command_id}


@router.get("/api/media/screens/{screen_id}/commands/{command_id}")
def command_answer(screen_id: int, command_id: str, request: Request, user=Depends(screens_user),
                   conn: sqlite3.Connection = Depends(get_db)):
    """How a command went: sent, waiting (someone is using the TV; seconds left), done, or kept."""
    _castable(request, conn, user, screen_id)
    answer = hub(request).answer_of(screen_id, command_id)
    if answer is None:
        raise _not_found("That command is too old.")
    return answer


@router.post("/api/media/screens/{screen_id}/commands/{command_id}/override")
async def override_command(screen_id: int, command_id: str, request: Request, user=Depends(screens_user),
                           conn: sqlite3.Connection = Depends(get_db)):
    """An admin: the TV stops waiting for the person using it and carries out the command now."""
    if not is_admin(request, conn, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can override someone using the TV.")
    screen = _castable(request, conn, user, screen_id)
    answer = hub(request).answer_of(screen_id, command_id)
    if answer is None or answer["state"] not in ("sent", "waiting"):
        raise HTTPException(status.HTTP_409_CONFLICT, "That command isn't waiting any more.")
    if not hub(request).push(screen_id, {"action": "override", "id": command_id, "sender": user["username"]}):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{screen['name']} isn't connected right now.")
    with transaction(conn):
        _log(conn, user, "screen.override", screen["name"])
    return {"status": "sent"}


class CommandAnswer(BaseModel):
    state: Literal["waiting", "done", "kept"]
    seconds: int | None = Field(None, ge=0, le=60)       # waiting: how long until it takes over


@router.post("/media/screen/commands/{command_id}")
async def screen_answer(command_id: str, body: CommandAnswer, request: Request, screen=Depends(screen_device)):
    """The TV says how a command went: waiting for the person using it, done, or kept by them."""
    if not hub(request).answer(screen["id"], command_id, body.model_dump(exclude_none=True)):
        raise _not_found()
    return {"status": "ok"}


@router.get("/media/screen/stream")
async def screen_stream(request: Request, screen=Depends(screen_device), conn: sqlite3.Connection = Depends(get_db)):
    """The screen's commands, as server-sent events, with a comment every 20 s so dead connections close."""
    response = _command_stream(request, conn, screen)
    # The key is renewed whenever the screen connects, so a TV in daily use never loses it.
    _key_cookie(response, request, request.cookies[screens.COOKIE])
    return response


def _command_stream(request: Request, conn: sqlite3.Connection, screen: sqlite3.Row) -> StreamingResponse:
    """A screen's (or a speaker's) commands as server-sent events, until it goes or is unpaired."""
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

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ScreenState(BaseModel):
    kind: Literal["idle", "video", "live", "youtube", "audio", "radio", "pictures", "signage", "calibrating",
                  "in_use"]                              # in_use: a box on a page other than the TV page
    title: str = Field("", max_length=300)
    sub: str = Field("", max_length=300)
    art: str | None = Field(None, max_length=300)
    position: float | None = None
    duration: float | None = None
    paused: bool = False
    volume: float | None = Field(None, ge=0, le=1)
    muted: bool = False
    has_queue: bool = False
    night: float | None = Field(None, ge=0, le=1)        # the night filter's level
    version: str | None = Field(None, max_length=40)     # the player's version (signage shows it per screen)
    errors: list[str] = Field(default_factory=list, max_length=10)   # slides that wouldn't load, lately


class SafeArea(BaseModel):
    x: float = Field(..., ge=0, le=screens.MAX_SAFE)
    y: float = Field(..., ge=0, le=screens.MAX_SAFE)


@router.put("/media/screen/safe-area")
def screen_safe_area(body: SafeArea, screen=Depends(screen_device), conn: sqlite3.Connection = Depends(get_db)):
    """The TV's own calibration (underscan), saved from its calibration screen."""
    with transaction(conn):
        x, y = screens.set_safe_area(conn, screen["id"], body.x, body.y)
    return {"x": x, "y": y}


@router.put("/api/media/screen/state")
async def screen_state(body: ScreenState, request: Request, screen=Depends(screen_device),
                       conn: sqlite3.Connection = Depends(get_db)):
    """What the screen is playing, every few seconds and on every change; read by its owner's remote, and
    kept (signage.record_state) so Access → Signage shows it and "online" outlives a restart."""
    return _record_state(request, conn, screen, body)


def _record_state(request: Request, conn: sqlite3.Connection, screen: sqlite3.Row, body: ScreenState) -> dict:
    state = body.model_dump(exclude={"version"})
    hub(request).set_state(screen["id"], state)
    with transaction(conn):
        signage.record_state(conn, screen["id"], state, body.version)
    return {"status": "ok"}


@router.get("/media/screen/signage")
def screen_signage(screen=Depends(screen_device), conn: sqlite3.Connection = Depends(get_db)):
    """The screen's programme and the playlists it names, ready to play (media/signage.py)."""
    return signage.for_screen(conn, screen)[0]


def _signage_file(conn: sqlite3.Connection, settings: Settings, allowed: set[int], file_id: int, page: int | None):
    if file_id not in allowed:
        raise _not_found()
    found = signage.picture_path(conn, settings, file_id, page)
    if found is None:
        raise _not_found()
    path, mime = found
    return FileResponse(path, media_type=mime, headers={
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "private, max-age=604800, immutable"})   # the address changes with the content


@router.get("/media/screen/signage/file/{file_id}")
def screen_signage_file(file_id: int, page: int | None = None, screen=Depends(screen_device),
                        conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """A picture (screen-sized), a PDF page or a video of the screen's playlists: only those, whatever its
    owner may read."""
    return _signage_file(conn, settings, signage.for_screen(conn, screen)[1], file_id, page)


# ------------------------------------------------------------------- remotes
def remote_device(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    """The remote (media/remotes.py) whose key came as `Authorization: Bearer …`, while its owner may use
    screens."""
    auth = request.headers.get("authorization", "")
    remote = remotes.by_key(conn, auth[7:].strip()) if auth.lower().startswith("bearer ") else None
    owner = users.get_user_by_id(conn, remote["user_id"]) if remote is not None else None
    if owner is None or not screens.may_use(conn, owner, request.app.state.directory):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This remote isn't paired",
                            headers={"WWW-Authenticate": "Bearer"})
    return remote


class RemotePairStart(BaseModel):
    speaker: bool = False                                # the panel has a speaker (media/speakers.py)


@router.post("/media/remote/pair")
async def start_remote_pairing(request: Request, body: RemotePairStart | None = None):
    """A new code for an unpaired remote to show, and the secret it collects its key with (in the body:
    the panel keeps no cookies). A panel with a speaker says so, and pairs as a speaker too."""
    try:
        code, secret = pairings(request).start("speaker" if body is not None and body.speaker else "remote")
    except screens.ScreenError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return {"code": screens.show_code(code), "expires_in": screens.PAIR_SECONDS,
            "url": _pairing_url(request, code), "secret": secret}


class RemotePairing(BaseModel):
    secret: str = Field(..., max_length=200)


@router.post("/media/remote/pair/status")
async def remote_pairing_status(body: RemotePairing, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """What the remote asks every few seconds: still waiting, paired (it gets its key, once), or expired."""
    p = pairings(request)
    for kind in ("remote", "speaker"):
        collected = p.collect(body.secret, kind)
        if collected is not None:
            remote = remotes.get(conn, collected[0])
            return {"state": "paired", "key": collected[1], "name": remote["name"] if remote else ""}
    found = p.waiting(body.secret, "remote") or p.waiting(body.secret, "speaker")
    if found is None:
        return {"state": "expired"}
    return {"state": "waiting", "expires_in": int(found[1])}


@router.get("/api/media/remote")
def remote_screens(request: Request, remote=Depends(remote_device), conn: sqlite3.Connection = Depends(get_db)):
    """The remote's name and its owner's screens, with what each is playing (no artwork: a small panel)."""
    with transaction(conn):
        remotes.seen(conn, remote["id"])
    h = hub(request)
    keep = ("kind", "title", "sub", "position", "duration", "paused", "volume", "muted", "night")

    def row(s: sqlite3.Row) -> dict:
        state = h.state(s["id"])
        return {"id": s["id"], "name": s["name"], "online": h.online(s["id"]), "speaker": bool(s["speaker"]),
                "state": {k: state.get(k) for k in keep} if state else None}
    mine = remotes.speaker_of(conn, remote)
    return {"name": remote["name"], "owner": remote["username"], "speaker": mine["id"] if mine else None,
            "screens": [row(s) for s in screens.of_user(conn, remote["user_id"])]}


@router.get("/api/media/remote/channels")
def remote_channels(offset: int = 0, limit: int = 5, remote=Depends(remote_device),
                    conn: sqlite3.Connection = Depends(get_db)):
    """Live streams, then radio stations, a page at a time: what the remote can start on a screen."""
    stations, _ = live.stations(conn)
    channels = [{"kind": "stream", "id": s["id"], "name": s["name"]}
                for s in live.sources(conn) if s["kind"] in ("rtmp", "hls", "youtube")]
    channels += [{"kind": "station", "key": s["key"], "name": s["name"]} for s in stations]
    offset, limit = max(0, offset), max(1, min(20, limit))
    return {"total": len(channels), "offset": offset, "channels": channels[offset:offset + limit]}


class RemoteCommand(BaseModel):
    action: Literal[remotes.ACTIONS]
    by: float | None = Field(None, ge=-36000, le=36000)  # skip
    level: float | None = Field(None, ge=0, le=1)        # volume; night (none: the next step)
    button: Literal[remotes.BUTTONS] | None = None       # press: up, down, left, right or enter
    source_id: int | None = None                         # play_source
    key: str | None = Field(None, max_length=2000)       # play_station


@router.post("/api/media/remote/screens/{screen_id}/command")
async def remote_command(screen_id: int, body: RemoteCommand, request: Request, remote=Depends(remote_device),
                         conn: sqlite3.Connection = Depends(get_db)):
    """Control one of the remote's owner's screens: as their phone would, but only these commands."""
    screen = screens.owned(conn, remote["user_id"], screen_id)
    if screen is None:
        raise _not_found()
    owner = users.get_user_by_id(conn, remote["user_id"])
    command = await anyio.to_thread.run_sync(_command, conn, owner, Command(**body.model_dump()))
    if screen["speaker"]:
        command = await anyio.to_thread.run_sync(_for_speaker, request, conn, owner, screen, command)
    command.update(sender=owner["username"], override=False)
    command_id = hub(request).send(screen_id, command)
    if command_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{screen['name']} isn't connected: is Media open on it?")
    return {"status": "sent", "id": command_id}


def _speaker_gone(request: Request, screen_id: int | None) -> None:
    """A removed remote's speaker: its stream ends and its tickets stop working."""
    if screen_id is not None:
        hub(request).close(screen_id)
        speaker_tickets(request).forget(screen_id)


@router.delete("/api/media/remote")
async def forget_remote(request: Request, remote=Depends(remote_device), conn: sqlite3.Connection = Depends(get_db)):
    """The panel forgets itself (its Forget button): its key stops working, and its speaker goes with it."""
    with transaction(conn):
        remotes.remove(conn, remote["id"], remote["user_id"])
    _speaker_gone(request, remote["screen_id"])
    return {"status": "removed"}


@router.delete("/api/media/remotes/{remote_id}")
async def remove_remote(remote_id: int, request: Request, user=Depends(api_user),
                        conn: sqlite3.Connection = Depends(get_db)):
    """Its owner removes a remote under Media → Screens (and its speaker, if it is one)."""
    found = conn.execute("SELECT screen_id FROM media_remotes WHERE id = ? AND user_id = ?", (remote_id, user["id"])).fetchone()
    with transaction(conn):
        if not remotes.remove(conn, remote_id, user["id"]):
            raise _not_found()
    _speaker_gone(request, found["screen_id"] if found else None)
    return {"status": "removed"}


# ------------------------------------------------------------------ speakers
def speaker_device(remote=Depends(remote_device), conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    """The speaker screen (media/speakers.py) of the remote whose key came; 404 for a remote without one."""
    screen = remotes.speaker_of(conn, remote)
    if screen is None:
        raise _not_found("This remote isn't a speaker")
    return screen


@router.get("/api/media/speaker/stream")
async def speaker_stream(request: Request, screen=Depends(speaker_device), conn: sqlite3.Connection = Depends(get_db)):
    """What is cast to the speaker, as server-sent events: the screen stream, opened with the remote's key."""
    return _command_stream(request, conn, screen)


@router.put("/api/media/speaker/state")
async def speaker_state(body: ScreenState, request: Request, screen=Depends(speaker_device),
                        conn: sqlite3.Connection = Depends(get_db)):
    """What the speaker is playing, as a TV reports it; the phones and panels show it."""
    return _record_state(request, conn, screen, body)


@router.post("/api/media/speaker/commands/{command_id}")
async def speaker_answer(command_id: str, body: CommandAnswer, request: Request, screen=Depends(speaker_device)):
    """The speaker says how a command went, as a TV does."""
    if not hub(request).answer(screen["id"], command_id, body.model_dump(exclude_none=True)):
        raise _not_found()
    return {"status": "ok"}


@router.get("/media/speaker/audio/{name}")
async def speaker_audio(name: str, request: Request, conn: sqlite3.Connection = Depends(get_db),
                        settings: Settings = Depends(get_settings)):
    """A track cast to a speaker, by its ticket alone (the panel's audio library can't send a header). The
    speaker must still be paired, and whoever cast the track still able to read it and use screens."""
    ticket = speaker_tickets(request).get(name)
    if ticket is None:
        raise _not_found()
    paired = conn.execute("SELECT 1 FROM media_remotes r JOIN media_screens s ON s.id = r.screen_id "
                          "WHERE s.id = ? AND s.speaker = 1", (ticket.screen_id,)).fetchone()
    user = users.get_user_by_id(conn, ticket.user_id)
    if paired is None or user is None or not user["active"] or not screens.may_use(conn, user, request.app.state.directory):
        raise _not_found()
    _readable_item(conn, user, ticket.file_id)
    path = store.content_path(conn, settings, ticket.file_id)
    if not path.is_file():
        raise _not_found("File not found")
    if ticket.convert:
        try:
            path = await anyio.to_thread.run_sync(speakers.converted, settings.media_ffmpeg,
                                                  settings.media_dir / "speaker", ticket.file_id, path)
        except speakers.SpeakerError as exc:
            raise HTTPException(exc.status, str(exc)) from None
    return FileResponse(path, media_type=speakers.MIME[ticket.ext], headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


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
    signage_folder: str | None = Field(None, max_length=120)   # the Signage folder at the top of the share
    signage_effects: dict | None = None                        # CRT effects for newly paired screens


@admin_router.put("/api/admin/settings/media")
def admin_settings(body: MediaSettingsBody, request: Request, user=Depends(admin_user),
                   conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Media on or off (on: the Media folder, with Music, Movies, TV and Pictures, made on the share), which
    folder it is, and its settings."""
    try:
        with transaction(conn):
            if body.folder is not None and media.set_folder(conn, settings, body.folder):
                _log(conn, user, "settings.media_folder", media.check_folder(body.folder))
                if media.enabled(conn) and body.enabled is not False:
                    media.ensure_library(conn)   # the new folder, and its Music, Movies, TV and Pictures
            if body.enabled is True:
                lib = media.ensure_library(conn)
                if not media.enabled(conn):
                    media.set_enabled(conn, True)
                    _log(conn, user, "settings.media", "on", lib["share_path"])
            elif body.enabled is False and media.enabled(conn):
                media.set_enabled(conn, False)
                _log(conn, user, "settings.media", "off")
            if body.signage_folder is not None and media.set_signage_folder(conn, settings, body.signage_folder):
                _log(conn, user, "settings.signage_folder", media.check_folder(body.signage_folder))
                if media.enabled(conn) and body.enabled is not False:
                    media.ensure_signage_library(conn)
            changes = body.model_dump(exclude={"enabled", "folder", "tmdb_key", "signage_folder"}, exclude_none=True)
            if changes.get("signage_effects") is not None:
                try:
                    changes["signage_effects"] = signage.clean_effects(changes["signage_effects"])
                except signage.SignageError as exc:
                    raise media.MediaError(str(exc)) from None
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



# ------------------------------------------------------------ admin: signage
def _signage_status(request: Request, conn: sqlite3.Connection) -> dict:
    h = hub(request)
    cfg = media.load_settings(conn)
    sg = media.signage_library(conn)
    return {"enabled": media.enabled(conn), "library": media.library(conn) is not None,
            "signage_library": {"id": sg["id"], "name": sg["name"]} if sg is not None else None,
            "settings": {"signage_folder": cfg["signage_folder"], "signage_effects": cfg["signage_effects"] or {}},
            "shows": signage.shows(conn), "groups": signage.groups(conn), "schedule": signage.schedule(conn),
            "tree": signage.tree(conn),
            "screens": [s | {"online": h.online(s["id"]) or s["recent"], "state": h.state(s["id"]) or s["last_state"]}
                        for s in signage.screens(conn)]}


def _signage_changed(request: Request, conn: sqlite3.Connection, screen_ids=None) -> None:
    """The screens fetch their signage again (cheap, and admins change it seldom): all, or the given ones."""
    h = hub(request)
    ids = screen_ids if screen_ids is not None else [r["id"] for r in conn.execute("SELECT id FROM media_screens")]
    for screen_id in ids:
        h.push(screen_id, {"action": "signage"})


def _signage_error(exc: signage.SignageError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _ensure_signage(conn: sqlite3.Connection) -> None:
    """The Signage folder on the share, made when Media is on and the share is there (quietly otherwise)."""
    if not media.enabled(conn) or media.signage_library(conn) is not None:
        return
    try:
        with transaction(conn):
            media.ensure_signage_library(conn)
    except (media.MediaError, OSError) as exc:
        log.info("Signage folder not made yet: %s", exc)


@admin_router.get("/api/admin/signage")
async def admin_signage(request: Request, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    _ensure_signage(conn)
    return _signage_status(request, conn)


@admin_router.get("/api/admin/signage/files")
def admin_signage_files(library: int, folder: int | None = None, user=Depends(admin_user),
                        conn: sqlite3.Connection = Depends(get_db)):
    try:
        return {"files": signage.files_in(conn, library, folder)}
    except signage.SignageError as exc:
        raise _signage_error(exc) from None


@admin_router.get("/api/admin/signage/art/{artwork_id}")
def admin_signage_art(artwork_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db),
                      settings: Settings = Depends(get_settings)):
    """A thumbnail from the catalog, for the Signage page (admins needn't be in the Media group)."""
    row = conn.execute("SELECT rel_path FROM media_artwork WHERE id = ?", (artwork_id,)).fetchone()
    path = artwork.path_of(settings, row["rel_path"]) if row else None
    if path is None or not path.is_file():
        raise _not_found()
    return FileResponse(path, media_type="image/jpeg", headers={
        "Cache-Control": "private, max-age=604800, immutable", "X-Content-Type-Options": "nosniff"})


@admin_router.get("/api/admin/signage/file/{file_id}")
def admin_signage_file(file_id: int, page: int | None = None, user=Depends(admin_user),
                       conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """A playlist's picture, PDF page or video as a screen gets it, for the admin page's preview."""
    return _signage_file(conn, settings, signage.admin_files(conn), file_id, page)


@admin_router.get("/api/admin/signage/preview/{show_id}")
def admin_signage_preview(show_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    try:
        return signage.preview(conn, show_id)[0]
    except signage.SignageError as exc:
        raise _signage_error(exc) from None


class NewFolderBody(BaseModel):
    library_id: int
    parent_id: int | None = None
    name: str = Field(..., max_length=200)


@admin_router.post("/api/admin/signage/folders", status_code=status.HTTP_201_CREATED)
def admin_signage_folder(body: NewFolderBody, request: Request, user=Depends(admin_user),
                         conn: sqlite3.Connection = Depends(get_db)):
    """A folder in the Signage folder (as the admin, with their share rights)."""
    from mnemerynne import fs, share
    sg = media.signage_library(conn)
    if sg is None or body.library_id != sg["id"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "New folders go in the Signage folder.")
    try:
        folder_id = fs.create_folder(conn, fs.Access.of(conn, user["id"]), sg["id"], body.parent_id, body.name)
    except fs.FsError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except share.ShareError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    _log(conn, user, "signage.folder_create", body.name.strip())
    conn.commit()
    return _signage_status(request, conn) | {"created": folder_id}


class NewShowBody(BaseModel):
    name: str = Field(..., max_length=200)


class ShowBody(BaseModel):
    name: str = Field(..., max_length=200)
    description: str = Field("", max_length=300)
    seconds: int = Field(10, ge=3, le=3600)
    shuffle: bool = False
    transition: Literal["fade", "cut", "slide", "zoom"] = "fade"
    fit: Literal["contain", "cover", "blur"] = "contain"
    effects: dict | None = None
    slides: list[dict] = Field(default_factory=list, max_length=signage.MAX_SLIDES)


class ScreenSignageBody(BaseModel):
    idle_minutes: int = Field(5, ge=1, le=1440)
    group_id: int | None = None
    location: str = Field("", max_length=80)
    orientation: Literal["landscape", "portrait"] = "landscape"
    effects: dict | None = None


class GroupBody(BaseModel):
    name: str = Field(..., max_length=200)


class ScreenCommandBody(BaseModel):
    action: Literal["reload", "identify", "signage_stop", "signage_start"]


@admin_router.post("/api/admin/signage/shows", status_code=status.HTTP_201_CREATED)
async def admin_new_show(body: NewShowBody, request: Request, user=Depends(admin_user),
                         conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            show_id = signage.create_show(conn, user["id"], body.name)
            _log(conn, user, "signage.show_create", signage.clean_name(body.name))
    except signage.SignageError as exc:
        raise _signage_error(exc) from None
    return _signage_status(request, conn) | {"created": show_id}


@admin_router.put("/api/admin/signage/shows/{show_id}")
async def admin_save_show(show_id: int, body: ShowBody, request: Request, user=Depends(admin_user),
                          conn: sqlite3.Connection = Depends(get_db)):
    try:
        slides = signage.parse_slides(body.slides)
        with transaction(conn):
            signage.update_show(conn, user["id"], show_id, name=body.name, description=body.description,
                                seconds=body.seconds, shuffle=body.shuffle, transition=body.transition, fit=body.fit,
                                effects=body.effects, slides=slides)
            _log(conn, user, "signage.show_update", signage.clean_name(body.name), f"{len(slides)} slide(s)")
    except signage.SignageError as exc:
        raise _signage_error(exc) from None
    _signage_changed(request, conn)
    return _signage_status(request, conn)


@admin_router.post("/api/admin/signage/shows/{show_id}/duplicate", status_code=status.HTTP_201_CREATED)
async def admin_duplicate_show(show_id: int, request: Request, user=Depends(admin_user),
                               conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            made = signage.duplicate_show(conn, user["id"], show_id)
            name = conn.execute("SELECT name FROM signage_shows WHERE id = ?", (made,)).fetchone()["name"]
            _log(conn, user, "signage.show_create", name, "a copy")
    except signage.SignageError as exc:
        raise _signage_error(exc) from None
    return _signage_status(request, conn) | {"created": made}


@admin_router.delete("/api/admin/signage/shows/{show_id}")
async def admin_delete_show(show_id: int, request: Request, user=Depends(admin_user),
                            conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute("SELECT name FROM signage_shows WHERE id = ?", (show_id,)).fetchone()
    if row is None:
        raise _not_found()
    with transaction(conn):
        signage.delete_show(conn, show_id)
        _log(conn, user, "signage.show_delete", row["name"])
    _signage_changed(request, conn)
    return _signage_status(request, conn)


@admin_router.post("/api/admin/signage/groups", status_code=status.HTTP_201_CREATED)
async def admin_new_group(body: GroupBody, request: Request, user=Depends(admin_user),
                          conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            group_id = signage.save_group(conn, None, body.name)
            _log(conn, user, "signage.group_create", signage.clean_name(body.name, "group", 40))
    except signage.SignageError as exc:
        raise _signage_error(exc) from None
    return _signage_status(request, conn) | {"created": group_id}


@admin_router.put("/api/admin/signage/groups/{group_id}")
async def admin_rename_group(group_id: int, body: GroupBody, request: Request, user=Depends(admin_user),
                             conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            signage.save_group(conn, group_id, body.name)
            _log(conn, user, "signage.group_update", signage.clean_name(body.name, "group", 40))
    except signage.SignageError as exc:
        raise _signage_error(exc) from None
    return _signage_status(request, conn)


@admin_router.delete("/api/admin/signage/groups/{group_id}")
async def admin_delete_group(group_id: int, request: Request, user=Depends(admin_user),
                             conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute("SELECT name FROM signage_groups WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise _not_found()
    with transaction(conn):
        members = signage.delete_group(conn, group_id)
        _log(conn, user, "signage.group_delete", row["name"])
    _signage_changed(request, conn, members)
    return _signage_status(request, conn)


@admin_router.post("/api/admin/signage/schedule", status_code=status.HTTP_201_CREATED)
async def admin_new_entry(body: signage.Entry, request: Request, user=Depends(admin_user),
                          conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            entry_id = signage.save_entry(conn, None, body)
            _log(conn, user, "signage.schedule_create", signage.entry_target_label(conn, body), _entry_detail(conn, body))
    except signage.SignageError as exc:
        raise _signage_error(exc) from None
    _signage_changed(request, conn)
    return _signage_status(request, conn) | {"created": entry_id}


@admin_router.put("/api/admin/signage/schedule/{entry_id}")
async def admin_save_entry(entry_id: int, body: signage.Entry, request: Request, user=Depends(admin_user),
                           conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            signage.save_entry(conn, entry_id, body)
            _log(conn, user, "signage.schedule_update", signage.entry_target_label(conn, body), _entry_detail(conn, body))
    except signage.SignageError as exc:
        raise _signage_error(exc) from None
    _signage_changed(request, conn)
    return _signage_status(request, conn)


@admin_router.delete("/api/admin/signage/schedule/{entry_id}")
async def admin_delete_entry(entry_id: int, request: Request, user=Depends(admin_user),
                             conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            signage.delete_entry(conn, entry_id)
            _log(conn, user, "signage.schedule_delete", str(entry_id))
    except signage.SignageError as exc:
        raise _signage_error(exc) from None
    _signage_changed(request, conn)
    return _signage_status(request, conn)


def _entry_detail(conn: sqlite3.Connection, e: signage.Entry) -> str:
    show = conn.execute("SELECT name FROM signage_shows WHERE id = ?", (e.show_id,)).fetchone()
    return f"{show['name'] if show else '?'} {e.start}-{e.end} {e.mode}" + ("" if e.enabled else " (off)")


@admin_router.put("/api/admin/signage/screens/{screen_id}")
async def admin_screen_signage(screen_id: int, body: ScreenSignageBody, request: Request, user=Depends(admin_user),
                               conn: sqlite3.Connection = Depends(get_db)):
    try:
        with transaction(conn):
            signage.set_screen(conn, screen_id, idle_minutes=body.idle_minutes, group_id=body.group_id,
                               location=body.location, orientation=body.orientation, effects=body.effects)
            name = conn.execute("SELECT name FROM media_screens WHERE id = ?", (screen_id,)).fetchone()["name"]
            _log(conn, user, "signage.screen", name, f"idle {body.idle_minutes} min, group {body.group_id or '-'}, "
                 f"{body.orientation}" + (", CRT" if body.effects and body.effects.get("crt") else ""))
    except signage.SignageError as exc:
        raise _signage_error(exc) from None
    hub(request).push(screen_id, {"action": "signage"})
    return _signage_status(request, conn)


@admin_router.post("/api/admin/signage/screens/{screen_id}/command")
async def admin_screen_command(screen_id: int, body: ScreenCommandBody, request: Request, user=Depends(admin_user),
                               conn: sqlite3.Connection = Depends(get_db)):
    """Reload the screen's page, show its name on it, or stop and start its signage (any screen, not only
    the admin's own: the owner's command route can't)."""
    row = conn.execute("SELECT name FROM media_screens WHERE id = ?", (screen_id,)).fetchone()
    if row is None:
        raise _not_found()
    # Someone using the TV (a box, on another page): an admin's command here doesn't wait for them.
    if hub(request).send(screen_id, {"action": body.action, "sender": user["username"], "override": True}) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{row['name']} isn't connected right now.")
    _log(conn, user, "signage.command", row["name"], body.action)
    conn.commit()
    return {"status": "sent"}
