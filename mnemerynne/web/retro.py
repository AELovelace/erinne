"""Media → Retro: the shelves of console games, the player page, the ROMs and box art, and each person's saves
(mnemerynne/media/retro.py). Part of Media, so it runs where Media runs (the media service, else the web app).

Everything answers 404 while Media or Retro is off, or for anyone outside the Media group, the same way as Media's
own routes; a ROM, a cover and a save answer only for someone who can read that ROM on the share.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response

from mnemerynne import media
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.ingest import store
from mnemerynne.media import retro
from mnemerynne.web.deps import get_db, get_media_db, get_settings, is_admin, page_user
from mnemerynne.web.media import _media_on, media_user
from mnemerynne.web.sendfile import send_file

# The player page loads the emulator's own scripts and its core (a .wasm made into a blob: script), and may only
# talk to this server: no update checks, no CDN fallback for a missing core, no netplay. 'unsafe-eval': the zip and
# 7z extractors (static/vendor/emulatorjs/compression, which open every core) are older Emscripten builds whose
# cwrap() evals; the page runs no script but this server's and the core's own blob:, and has no inline script.
PLAYER_CSP = ("default-src 'self'; script-src 'self' blob: 'wasm-unsafe-eval' 'unsafe-eval'; worker-src 'self' blob:; "
              "connect-src 'self' blob: data:; img-src 'self' blob: data:; media-src 'self' blob:; "
              "style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; base-uri 'self'")
_SHOT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


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


def _rom_name(row: sqlite3.Row) -> str:
    """What the emulator calls the ROM: named by its content, so each game's own save (kept by the browser
    under this name too) is that game's, and its suffix still tells a .zip from a .gba."""
    return row["blob_sha256"][:16] + Path(row["display_name"]).suffix.lower()


def _consoles() -> list[dict]:
    return [{"key": c.key, "name": c.name, "folder": c.folder} for c in retro.CONSOLES.values()]


# -------------------------------------------------------------------- pages
@router.get("/media/retro", response_class=HTMLResponse)
def retro_page(request: Request, user=Depends(page_user), conn: sqlite3.Connection = Depends(get_media_db)):
    lib = retro.library(conn)
    return request.app.state.templates.TemplateResponse(request, "retro.html", {
        "user": user, "is_admin": is_admin(request, conn, user), "active": "media",
        "folder": lib["share_path"] if lib is not None else media.load_settings(conn)["retro_folder"],
        "consoles": _consoles()})


@router.get("/media/retro/play/{file_id}", response_class=HTMLResponse)
def player_page(file_id: int, request: Request, user=Depends(page_user),
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
    response = request.app.state.templates.TemplateResponse(request, "retro_play.html", {
        # In a <script> block: a file name holding "</script>" mustn't end it, so no "<" goes in as it is.
        "user": user, "game": game, "game_json": json.dumps(game).replace("<", "\\u003c")})
    response.headers["Content-Security-Policy"] = PLAYER_CSP
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
def list_saves(file_id: int, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_media_db)):
    row, _ = _rom(conn, user, file_id)
    return {"saves": retro.saves(conn, user["id"], row["blob_sha256"])}


def _refuse(exc: retro.RetroError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@router.get("/api/media/retro/saves/{file_id}/{slot}")
def get_save(file_id: int, slot: str, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_media_db)):
    """One slot's data, as it was saved ('sram': the game's own save; 'auto', '1' to '4': save states)."""
    row, _ = _rom(conn, user, file_id)
    try:
        body = retro.get_save(conn, user["id"], row["blob_sha256"], slot)
    except retro.RetroError as exc:
        raise _refuse(exc) from None
    if body is None:
        raise _not_found("No save there")
    return Response(body, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})


@router.get("/api/media/retro/saves/{file_id}/{slot}/shot")
def get_shot(file_id: int, slot: str, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_media_db)):
    row, _ = _rom(conn, user, file_id)
    try:
        body = retro.get_save(conn, user["id"], row["blob_sha256"], slot, shot=True)
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
async def put_save(file_id: int, slot: str, request: Request, user=Depends(media_user),
                   conn: sqlite3.Connection = Depends(get_media_db)):
    """Keep a slot: the body is the save itself (application/octet-stream), replacing what the slot held."""
    row, _ = _rom(conn, user, file_id)
    data = await _body(request, retro.MAX_SRAM if slot == "sram" else retro.MAX_STATE)
    try:
        with transaction(conn):
            retro.put_save(conn, user["id"], row["blob_sha256"], slot, data)
    except retro.RetroError as exc:
        raise _refuse(exc) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/api/media/retro/saves/{file_id}/{slot}/shot", status_code=status.HTTP_204_NO_CONTENT)
async def put_shot(file_id: int, slot: str, request: Request, user=Depends(media_user),
                   conn: sqlite3.Connection = Depends(get_media_db)):
    """A save state's screenshot (a PNG), sent after the state itself."""
    row, _ = _rom(conn, user, file_id)
    png = await _body(request, retro.MAX_SHOT)
    try:
        with transaction(conn):
            kept = retro.put_shot(conn, user["id"], row["blob_sha256"], slot, png)
    except retro.RetroError as exc:
        raise _refuse(exc) from None
    if not kept:
        raise _not_found("No save there")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/media/retro/saves/{file_id}/{slot}", status_code=status.HTTP_204_NO_CONTENT)
def delete_save(file_id: int, slot: str, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_media_db)):
    row, _ = _rom(conn, user, file_id)
    try:
        with transaction(conn):
            gone = retro.delete_save(conn, user["id"], row["blob_sha256"], slot)
    except retro.RetroError as exc:
        raise _refuse(exc) from None
    if not gone:
        raise _not_found("No save there")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
