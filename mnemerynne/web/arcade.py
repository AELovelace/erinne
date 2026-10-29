"""The main origin's side of the arcade (mnemerynne/arcade.py): Media → Games (the list, and a ticket into a game,
for a person or a screen), a ticket by path, and the admin's Games settings and games (Settings → Entertainment
→ Games)."""

from __future__ import annotations

import sqlite3

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from mnemerynne import arcade, group_refs, remote
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import _session_user, admin_user, api_user, get_db, get_settings, is_admin
from mnemerynne.web.media import _media_on, _screen, media_user

router = APIRouter()
media_router = APIRouter(dependencies=[Depends(_media_on)])   # Media → Games: only where Media is open
admin_router = APIRouter()

TEST_SECONDS = 8.0


def _ticket_url(settings: Settings, token: str) -> str:
    return f"{arcade.origin(settings)}{arcade.OWN_PREFIX}enter?t={token}"


def _where(settings: Settings) -> str:
    where = arcade.origin(settings)
    if not where:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The arcade isn't set up on this server (MNEMERYNNE_ARCADE_URL).")
    return where


class TicketBody(BaseModel):
    path: str = Field("/", max_length=500)   # the page on the games site to open, e.g. /lumibot/


@router.post("/api/arcade/ticket")
def ticket(body: TicketBody, request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db),
           settings: Settings = Depends(get_settings)):
    """A one-use, one-minute address that signs this person into the arcade at `path`."""
    _where(settings)
    try:
        path = arcade.clean_path(body.path)
    except arcade.ArcadeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    if not arcade.may_play(conn, user, request.app.state.directory, is_admin(request, conn, user)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Games aren't on for you.")
    token = arcade.make_ticket(request.app.state.arcade.secret_key, user, path)
    return {"url": _ticket_url(settings, token), "expires_in": arcade.TICKET_SECONDS}


# --------------------------------------------------------------- Media → Games
def _player(request: Request, conn: sqlite3.Connection, user: sqlite3.Row) -> tuple[bool, int | None]:
    """(admin, screen id) for whoever is asking: a signed-in person, or a screen (the TV) playing as its owner."""
    if _session_user(request, conn) is not None:
        return is_admin(request, conn, user), None
    screen = _screen(request, conn)
    return False, screen["id"] if screen is not None else None


@media_router.get("/api/media/games")
def media_games(request: Request, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    """The games this person (or screen) may open; none while the arcade isn't set up."""
    if not arcade.origin(settings):
        return {"games": []}
    admin, _ = _player(request, conn, user)
    return {"games": arcade.games_for(conn, user, request.app.state.directory, admin)}


@media_router.post("/api/media/games/{slug}/ticket")
def media_game_ticket(slug: str, request: Request, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db),
                      settings: Settings = Depends(get_settings)):
    """A one-use address into one game: its start page, for this person, or for this screen and its owner."""
    _where(settings)
    admin, screen_id = _player(request, conn, user)
    game = next((g for g in arcade.games_for(conn, user, request.app.state.directory, admin) if g["slug"] == slug), None)
    if game is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such game, or it isn't open for you.")
    token = arcade.make_ticket(request.app.state.arcade.secret_key, user, game["start_path"], screen_id)
    return {"url": _ticket_url(settings, token), "expires_in": arcade.TICKET_SECONDS, "game": game}


# -------------------------------------------------------------------- admin
def _status(conn: sqlite3.Connection, settings: Settings) -> dict:
    cfg = arcade.load_settings(conn)
    return {"settings": cfg | {"group": group_refs.label(conn, cfg["group"])},
            "arcade_url": arcade.origin(settings), "games": arcade.list_games(conn)}


class GamesSettingsBody(BaseModel):
    enabled: bool | None = None
    group: str | None = Field(None, max_length=1000)     # who may play: AD or Entra group(s), separated by ;
    site_url: str | None = Field(None, max_length=500)   # the site to proxy, e.g. https://sadgirlsclub.wtf


@admin_router.get("/api/admin/settings/games")
def admin_games(user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    return _status(conn, settings)


@admin_router.put("/api/admin/settings/games")
def admin_save_games(body: GamesSettingsBody, request: Request, user=Depends(admin_user),
                     conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Games on or off, who may play, and the site; only the fields sent change. Logged."""
    changes = body.model_dump(exclude_none=True)
    try:
        if "site_url" in changes:
            changes["site_url"] = arcade.clean_site_url(changes["site_url"])
        if "group" in changes:
            directory = request.app.state.directory
            changes["group"] = group_refs.resolve(conn, changes["group"], directory.groups() if directory.enabled else [])
    except (arcade.ArcadeError, group_refs.GroupRefError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    if changes:
        with transaction(conn):
            arcade.save_settings(conn, changes)
            _log(conn, user, "settings.games", ", ".join(f"{k}={v}" for k, v in changes.items()))
    return _status(conn, settings)


class GameBody(BaseModel):
    name: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=1000)
    icon: str | None = Field(None, max_length=40)
    start_path: str | None = Field(None, max_length=500)   # the game's page on the site, e.g. /lumibot/
    enabled: bool | None = None
    move: int | None = Field(None, ge=-1, le=1)            # -1 up, +1 down the list


def _game_change(conn, user, action: str, fn) -> None:
    try:
        with transaction(conn):
            target = fn()
            _log(conn, user, action, target)
    except arcade.ArcadeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


@admin_router.post("/api/admin/games")
def admin_add_game(body: GameBody, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db),
                   settings: Settings = Depends(get_settings)):
    _game_change(conn, user, "games.add",
                 lambda: arcade.add_game(conn, body.model_dump(exclude_none=True, exclude={"move"}))["name"])
    return _status(conn, settings)


@admin_router.put("/api/admin/games/{game_id}")
def admin_change_game(game_id: int, body: GameBody, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db),
                      settings: Settings = Depends(get_settings)):
    def change():
        game = arcade.update_game(conn, game_id, body.model_dump(exclude_none=True, exclude={"move"}))
        if body.move:
            arcade.move_game(conn, game_id, body.move)
        return game["name"]
    _game_change(conn, user, "games.change", change)
    return _status(conn, settings)


@admin_router.delete("/api/admin/games/{game_id}")
def admin_remove_game(game_id: int, user=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db),
                      settings: Settings = Depends(get_settings)):
    def remove():
        row = conn.execute("SELECT name FROM games WHERE id = ?", (game_id,)).fetchone()
        if row is None or not arcade.delete_game(conn, game_id):
            raise arcade.ArcadeError("There's no such game.")
        return row["name"]
    _game_change(conn, user, "games.remove", remove)
    return _status(conn, settings)


@admin_router.post("/api/admin/games/{game_id}/test")
async def admin_test_game(game_id: int, request: Request, user=Depends(admin_user),
                          settings: Settings = Depends(get_settings)):
    """Ask the site for the game's start page as the arcade would (its host in X-Forwarded-Host): the status and
    where it redirects. Nothing is signed in, so a login redirect is a good answer."""
    from mnemerynne import db

    conn = db.connect(settings.db_path)
    try:
        row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        site = arcade.load_settings(conn)["site_url"]
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such game.")
    if not site:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Set the games site first.")
    headers = {"X-Forwarded-Host": arcade.host(settings), "X-Forwarded-Proto": "https" if arcade.origin(settings).startswith("https") else "http",
               "User-Agent": "Mnemerynne arcade test"}
    try:
        async with httpx.AsyncClient(transport=remote.TRANSPORT, follow_redirects=False, timeout=TEST_SECONDS) as client:
            got = await client.get(site + row["start_path"], headers=headers)
    except httpx.HTTPError as exc:
        return {"ok": False, "status": None, "detail": f"{site} didn't answer: {exc.__class__.__name__}"}
    location = got.headers.get("location", "")
    return {"ok": got.status_code < 400, "status": got.status_code,
            "location": remote.rewrite_url(location, arcade._site_row(site), "arcade", "") if location else ""}
