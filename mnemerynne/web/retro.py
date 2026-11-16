"""Media → Retro: the shelves of console games, the player page, the ROMs and box art, and each person's saves
(mnemerynne/media/retro.py). Part of Media, so it runs where Media runs (the media service, else the web app).

Everything answers 404 while Media or Retro is off, or for anyone outside the Media group, the same way as Media's
own routes; a ROM, a cover and a save answer only for someone who can read that ROM on the share.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse, Response

from mnemerynne import media, users
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.ingest import store
from mnemerynne.media import netplay, retro, screens
from mnemerynne.web.deps import LoginRequired, _session_user, feature_pool, get_db, get_media_db, get_settings, is_admin
from mnemerynne.web.media import _media_on, media_user
from mnemerynne.web.sendfile import send_file

# The player page loads the emulator's own scripts and its core (a .wasm made into a blob: script), and may only
# talk to this server: no update checks, no CDN fallback for a missing core, no EmulatorJS netplay (Retro's own,
# retro-netplay.js, is a websocket to this same host: named in connect-src by player_csp, as some Firefox versions
# don't count ws: / wss: to the page's own host as 'self'). 'unsafe-eval': the zip and
# 7z extractors (static/vendor/emulatorjs/compression, which open every core) are older Emscripten builds whose
# cwrap() evals; the page runs no script but this server's and the core's own blob:, and has no inline script.
PLAYER_CSP = ("default-src 'self'; script-src 'self' blob: 'wasm-unsafe-eval' 'unsafe-eval'; worker-src 'self' blob:; "
              "connect-src 'self' blob: data:; img-src 'self' blob: data:; media-src 'self' blob:; "
              "style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; base-uri 'self'")
_SHOT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


_HOST = re.compile(r"[A-Za-z0-9.-]+(:[0-9]{1,5})?|\[[0-9A-Fa-f:.]+\](:[0-9]{1,5})?")   # a name or an [IPv6], and a port


def player_csp(request: Request) -> str:
    """PLAYER_CSP with this host's websocket (netplay) in connect-src, for whichever address the page came by."""
    host = request.headers.get("host", "")
    if not _HOST.fullmatch(host):
        return PLAYER_CSP                           # not a plain host[:port]: 'self' alone
    return PLAYER_CSP.replace("connect-src 'self' ", f"connect-src 'self' wss://{host} ws://{host} ", 1)


def _retro_on(conn: sqlite3.Connection = Depends(get_db)) -> None:
    """Not there at all until an admin turns Retro on (Settings → Entertainment → Media → Retro)."""
    if not retro.enabled(conn):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


router = APIRouter(dependencies=[Depends(_media_on), Depends(_retro_on)])


def _not_found(what: str = "Not found") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, what)


def _rom(conn: sqlite3.Connection, user, file_id: int) -> tuple[sqlite3.Row, retro.Console]:
    found = retro.Shelf(conn, user["id"]).rom(file_id)
    if found is None:
        raise _not_found("Game not found")
    return found


def _save_key(conn: sqlite3.Connection, settings: Settings, user, file_id: int, member: str | None) -> str:
    """What a game's saves are kept under: the ROM's content (its sha256), or for one ROM inside an archive that
    holds several (`member`, "Which ROM?"), that ROM's own key, so a hack's save never lands on the original's."""
    row, console = _rom(conn, user, file_id)
    if not member:
        return row["blob_sha256"]
    try:
        inside = retro.archive_roms(store.content_path(conn, settings, row["id"]), console, row["blob_sha256"])
    except OSError:
        inside = []
    if member not in inside:
        raise _not_found("That ROM isn't in this archive")
    return retro.member_key(row["blob_sha256"], member)


def _rom_name(row: sqlite3.Row) -> str:
    """What the emulator calls the ROM: named by its content, so each game's own save (kept by the browser
    under this name too) is that game's, and its suffix still tells a .zip from a .gba."""
    return row["blob_sha256"][:16] + Path(row["display_name"]).suffix.lower()


def _consoles() -> list[dict]:
    return [{"key": c.key, "name": c.name, "folder": c.folder} for c in retro.CONSOLES.values()]


# -------------------------------------------------------------------- pages
def retro_page_user(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    """Whoever is signed in; or on a paired TV (2026-11-14: Retro in the TV's game layer), the TV's owner, as for
    the rest of Media. Neither: the sign-in page."""
    user = _session_user(request, conn)
    if user is not None:
        return user
    try:
        return media_user(request, conn)
    except HTTPException:
        raise LoginRequired() from None


def _on_tv(request: Request, conn: sqlite3.Connection) -> bool:
    """Shown on a TV by its screen key (nobody signed in): no site menu, and none of the owner's messages."""
    return _session_user(request, conn) is None


@router.get("/media/retro", response_class=HTMLResponse)
def retro_page(request: Request, user=Depends(retro_page_user), conn: sqlite3.Connection = Depends(get_media_db)):
    lib = retro.library(conn)
    tv = _on_tv(request, conn)
    return request.app.state.templates.TemplateResponse(request, "retro.html", {
        "user": None if tv else user, "on_tv": tv, "is_admin": False if tv else is_admin(request, conn, user), "active": "media",
        "folder": lib["share_path"] if lib is not None else media.load_settings(conn)["retro_folder"],
        "consoles": _consoles()})


@router.get("/media/retro/play/{file_id}", response_class=HTMLResponse)
def player_page(file_id: int, request: Request, user=Depends(retro_page_user),
                conn: sqlite3.Connection = Depends(get_media_db), settings: Settings = Depends(get_settings)):
    shelf = retro.Shelf(conn, user["id"])
    found = shelf.rom(file_id)
    if found is None:
        raise _not_found("Game not found")
    row, console = found
    tracks, missing = shelf.tracks(row, settings)      # a .cue's .bin tracks, which the emulator fetches first
    bios = shelf.bios(console)
    game = {"id": row["id"], "title": retro.title_of(row["display_name"]), "console": console.key,
            "console_name": console.name, "system": console.system, "core": console.core_for(row["display_name"]),
            "save_folder": retro.SAVE_FOLDERS.get(console.core_for(row["display_name"]), ""),
            "rom": f"/media/retro/rom/{row['id']}/{_rom_name(row)}", "slots": list(retro.STATE_SLOTS[1:]),
            "saves": retro.saves(conn, user["id"], row["blob_sha256"]),
            # Where each track goes in the emulator's files (beside the .cue, under the name the .cue gives it).
            "files": {"/" + name: f"/media/retro/track/{row['id']}/{n}/{quote(Path(name).name)}"
                      for n, (name, _) in enumerate(tracks)},
            "missing": missing,
            # The BIOS goes beside the game under its own name, which is what the core looks for.
            "bios": f"/media/retro/bios/{bios['id']}/{bios['display_name'].lower()}" if bios is not None else None,
            "needs_bios": console.needs_bios(row["display_name"]) and bios is None,
            "bios_names": list(console.bios)}
    # An archive holding several ROMs (an original and its hacks): "Which ROM?" first, each with its own saves.
    try:
        inside = retro.archive_roms(store.content_path(conn, settings, row["id"]), console, row["blob_sha256"])
    except OSError:
        inside = []
    if len(inside) > 1:
        ordered = retro.original_first(inside)          # the original first: folders of hacks can hold dozens
        game["members"] = [{"name": n, "title": retro.title_of(Path(n).name),
                            "original": i == 0 and retro.looks_original(n),
                            "saves": retro.saves(conn, user["id"], retro.member_key(row["blob_sha256"], n))}
                           for i, n in enumerate(ordered)]
        game["rom_exts"] = sorted(e.lstrip(".") for e in console.exts)   # what counts as a ROM when it's unpacked
    response = request.app.state.templates.TemplateResponse(request, "retro_play.html", {
        # In a <script> block: a file name holding "</script>" mustn't end it, so no "<" goes in as it is.
        "user": None if _on_tv(request, conn) else user, "game": game, "game_json": json.dumps(game).replace("<", "\\u003c")})
    response.headers["Content-Security-Policy"] = player_csp(request)
    return response


# --------------------------------------------------------------------- API
@router.get("/api/media/retro/games")
def games(user=Depends(media_user), conn: sqlite3.Connection = Depends(get_media_db)):
    shelf = retro.Shelf(conn, user["id"])
    return {"library": shelf.lib is not None and shelf.lib["id"] in shelf.access.readable,
            "consoles": _consoles(), "games": shelf.games()}


@router.api_route("/media/retro/rom/{file_id}/{name}", methods=["GET", "HEAD"])   # HEAD: the emulator asks the size first
def rom_file(file_id: int, name: str, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_media_db),
             settings: Settings = Depends(get_settings)):
    """The ROM's bytes, for the emulator. The name in the path is _rom_name; another one is refused, so an old
    link never hands the emulator a different game under a known game's name."""
    row, _ = _rom(conn, user, file_id)
    if name != _rom_name(row):
        raise _not_found("Game not found")
    path = store.content_path(conn, settings, file_id)
    if not path.is_file():
        raise _not_found("Game not found")
    return send_file(path, media_type="application/octet-stream", filename=name, content_disposition_type="inline",
                     headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"})


@router.api_route("/media/retro/track/{file_id}/{n}/{name}", methods=["GET", "HEAD"])
def track_file(file_id: int, n: int, name: str, user=Depends(media_user),
               conn: sqlite3.Connection = Depends(get_media_db), settings: Settings = Depends(get_settings)):
    """The nth file a .cue game names (a .bin track beside it), for the emulator; the name is only for the address."""
    shelf = retro.Shelf(conn, user["id"])
    found = shelf.rom(file_id)
    tracks = shelf.tracks(found[0], settings)[0] if found is not None else []
    if not 0 <= n < len(tracks):
        raise _not_found("Track not found")
    path = store.content_path(conn, settings, tracks[n][1]["id"])
    if not path.is_file():
        raise _not_found("Track not found")
    return send_file(path, media_type="application/octet-stream", headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"})


@router.api_route("/media/retro/bios/{file_id}/{name}", methods=["GET", "HEAD"])
def bios_file(file_id: int, name: str, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_media_db),
              settings: Settings = Depends(get_settings)):
    """A console's BIOS from its folder (see Console.bios), under the name the core looks for."""
    shelf = retro.Shelf(conn, user["id"])
    row = next((b for c in retro.CONSOLES.values() if (b := shelf.bios(c)) is not None and b["id"] == file_id), None)
    if row is None or name != row["display_name"].lower():
        raise _not_found()
    path = store.content_path(conn, settings, file_id)
    if not path.is_file():
        raise _not_found()
    return send_file(path, media_type="application/octet-stream", headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"})


@router.get("/media/retro/cover/{file_id}")
def cover(file_id: int, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_media_db),
          settings: Settings = Depends(get_settings)):
    """A game's box art: the picture beside its ROM with the same name."""
    row = retro.Shelf(conn, user["id"]).cover(file_id)
    path = store.content_path(conn, settings, file_id) if row is not None else None
    if path is None or not path.is_file():
        raise _not_found()
    return send_file(path, media_type=_SHOT_TYPES[Path(row["display_name"]).suffix.lower()], headers={
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "private, max-age=3600"})


@router.post("/api/media/retro/played/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def played(file_id: int, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_media_db)):
    """The game started: it goes to the top of Continue playing."""
    row, _ = _rom(conn, user, file_id)
    with transaction(conn):
        retro.played(conn, user["id"], row["blob_sha256"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/media/retro/saves/{file_id}")
def list_saves(file_id: int, member: str | None = None, user=Depends(media_user),
               conn: sqlite3.Connection = Depends(get_media_db), settings: Settings = Depends(get_settings)):
    key = _save_key(conn, settings, user, file_id, member)
    return {"saves": retro.saves(conn, user["id"], key)}


def _refuse(exc: retro.RetroError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@router.get("/api/media/retro/saves/{file_id}/{slot}")
def get_save(file_id: int, slot: str, member: str | None = None, user=Depends(media_user),
             conn: sqlite3.Connection = Depends(get_media_db), settings: Settings = Depends(get_settings)):
    """One slot's data, as it was saved ('sram': the game's own save; 'auto', '1' to '4': save states)."""
    key = _save_key(conn, settings, user, file_id, member)
    try:
        body = retro.get_save(conn, user["id"], key, slot)
    except retro.RetroError as exc:
        raise _refuse(exc) from None
    if body is None:
        raise _not_found("No save there")
    return Response(body, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})


@router.get("/api/media/retro/saves/{file_id}/{slot}/shot")
def get_shot(file_id: int, slot: str, member: str | None = None, user=Depends(media_user),
             conn: sqlite3.Connection = Depends(get_media_db), settings: Settings = Depends(get_settings)):
    key = _save_key(conn, settings, user, file_id, member)
    try:
        body = retro.get_save(conn, user["id"], key, slot, shot=True)
    except retro.RetroError as exc:
        raise _refuse(exc) from None
    if body is None:
        raise _not_found("No screenshot there")
    return Response(body, media_type="image/png", headers={"Cache-Control": "no-store",
                                                            "X-Content-Type-Options": "nosniff"})


async def _body(request: Request, limit: int) -> bytes:
    """The raw request body, refused once it passes limit (read in pieces, so a huge one isn't held whole)."""
    data = bytearray()
    async for piece in request.stream():
        data += piece
        if len(data) > limit:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"A save is at most {limit >> 20} MB.")
    return bytes(data)


@router.put("/api/media/retro/saves/{file_id}/{slot}", status_code=status.HTTP_204_NO_CONTENT)
async def put_save(file_id: int, slot: str, request: Request, member: str | None = None, user=Depends(media_user),
                   conn: sqlite3.Connection = Depends(get_media_db), settings: Settings = Depends(get_settings)):
    """Keep a slot: the body is the save itself (application/octet-stream), replacing what the slot held."""
    key = _save_key(conn, settings, user, file_id, member)
    data = await _body(request, retro.MAX_SRAM if slot == "sram" else retro.MAX_STATE)
    try:
        with transaction(conn):
            retro.put_save(conn, user["id"], key, slot, data)
    except retro.RetroError as exc:
        raise _refuse(exc) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/api/media/retro/saves/{file_id}/{slot}/shot", status_code=status.HTTP_204_NO_CONTENT)
async def put_shot(file_id: int, slot: str, request: Request, member: str | None = None, user=Depends(media_user),
                   conn: sqlite3.Connection = Depends(get_media_db), settings: Settings = Depends(get_settings)):
    """A save state's screenshot (a PNG), sent after the state itself."""
    key = _save_key(conn, settings, user, file_id, member)
    png = await _body(request, retro.MAX_SHOT)
    try:
        with transaction(conn):
            kept = retro.put_shot(conn, user["id"], key, slot, png)
    except retro.RetroError as exc:
        raise _refuse(exc) from None
    if not kept:
        raise _not_found("No save there")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/media/retro/saves/{file_id}/{slot}", status_code=status.HTTP_204_NO_CONTENT)
def delete_save(file_id: int, slot: str, member: str | None = None, user=Depends(media_user),
             conn: sqlite3.Connection = Depends(get_media_db), settings: Settings = Depends(get_settings)):
    key = _save_key(conn, settings, user, file_id, member)
    try:
        with transaction(conn):
            gone = retro.delete_save(conn, user["id"], key, slot)
    except retro.RetroError as exc:
        raise _refuse(exc) from None
    if not gone:
        raise _not_found("No save there")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- netplay
# Playing together (media/netplay.py rooms, static/retro-netplay.js lockstep). The room list answers like the rest
# of Retro; the room itself is a websocket on its own router, as a websocket has no request for the router's guards.
@router.get("/api/media/retro/netplay/rooms")
def netplay_rooms(game: int | None = None, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_media_db)):
    """The rooms open for a game this person can read (`game`), or for every game they can."""
    shelf = retro.Shelf(conn, user["id"])
    if game is not None:
        if shelf.rom(game) is None:
            raise _not_found("Game not found")
        ids = [game]
    else:
        ids = [g["id"] for g in shelf.games()]
    return {"rooms": netplay.ROOMS.open_for(ids), "max": netplay.MAX_PLAYERS}


netplay_router = APIRouter()


def _netplay_check(websocket: WebSocket, conn: sqlite3.Connection, game: int) -> tuple[sqlite3.Row, sqlite3.Row, str] | None:
    """(the person, the ROM, its console) for someone who may play this game here, as Retro's routes check; else None.
    A paired TV plays as its owner, as everywhere in Media."""
    directory = websocket.app.state.directory
    if not media.enabled(conn) or not retro.enabled(conn):
        return None
    user = _session_user(websocket, conn)
    if user is not None:
        if not media.may_use(conn, user, directory):
            return None
    else:
        screen = screens.by_token(conn, websocket.cookies.get(screens.COOKIE))
        user = users.get_user_by_id(conn, screen["user_id"]) if screen is not None else None
        if user is None or not user["active"] or not screens.may_use(conn, user, directory):
            return None
    found = retro.Shelf(conn, user["id"]).rom(game)
    return (user, found[0], found[1].key) if found is not None else None


@netplay_router.websocket("/api/media/retro/netplay/ws")
async def netplay_socket(websocket: WebSocket, game: int, room: str = ""):
    """A seat in a room: `room` empty opens one for `game` (this person hosts), a code joins it. Then every message
    goes to the others (netplay.Rooms.relay) until this page goes."""
    pool = feature_pool(websocket.app, "media")
    conn = await anyio.to_thread.run_sync(pool.get)
    try:
        allowed = await anyio.to_thread.run_sync(_netplay_check, websocket, conn, game)
    finally:
        pool.put(conn)
    if allowed is None:
        await websocket.close(code=4403)              # not theirs to play: the page says so
        return
    user, rom, console = allowed
    await websocket.accept()
    lock = asyncio.Lock()                             # one send at a time: two others may relay to us at once

    async def send(message: str | bytes) -> None:
        async with lock:
            if isinstance(message, bytes):
                await websocket.send_bytes(message)
            else:
                await websocket.send_text(message)

    title = retro.title_of(rom["display_name"])
    try:
        if room:
            seat = await netplay.ROOMS.join(room, game_id=game, user_id=user["id"], name=user["username"], send=send)
        else:
            seat = await netplay.ROOMS.open(game_id=game, title=title, console=console, user_id=user["id"],
                                            name=user["username"], send=send)
    except netplay.NetplayError as exc:
        await send(json.dumps({"t": "error", "why": str(exc)}))
        await websocket.close()
        return
    the_room, me = seat
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            await netplay.ROOMS.relay(the_room, me, data if data is not None else message.get("text") or "")
    except WebSocketDisconnect:
        pass
    finally:
        await netplay.ROOMS.leave(the_room, me)
