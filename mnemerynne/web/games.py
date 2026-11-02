"""Media → Games: the built-in games (mnemerynne/games.py). The list for Media's Games view, and each game's page,
which Media's game layer frames (media.js games). Both sit behind Media's own guard (media._media_on): Media on,
and the person (or the TV's owner) in the Media group."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from mnemerynne import casino, casino_remote, games
from mnemerynne.media import remotes, screens
from mnemerynne.web.deps import _session_user, get_db
from mnemerynne.web.media import _media_on, _screen, media_user, screen_device

def _uncached(response: Response):
    """A shared URL still represents this caller's seat and the current round, never a cached snapshot."""
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(dependencies=[Depends(_media_on), Depends(_uncached)])


@router.get("/api/media/games")
def media_games(user=Depends(media_user)):
    """The games Media → Games shows, for a signed-in person or a paired screen (the TV)."""
    return {"games": games.listing()}


@router.get("/media/games/{slug}", response_class=HTMLResponse)
def game_page(slug: str, request: Request, user=Depends(media_user), conn=Depends(get_db)):
    """A game's page: its canvas and its scripts (static/games/<slug>/), in the person's theme. No `user` in the
    template, so the page carries none of the app's own scripts (messages, notifications) inside the game frame."""
    game = games.get(slug)
    if game is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such game")
    paired = _screen(request, conn)
    tv_play = paired is not None and not paired["speaker"] and paired["user_id"] == user["id"] and screens.may_use(conn, user, request.app.state.directory)
    return request.app.state.templates.TemplateResponse(request, "game.html", {"game": game, "tv_play": tv_play})


class TableCreate(BaseModel):
    """TV mode is explicit so existing single-seat and cross-device tables keep their controls."""
    model_config = ConfigDict(extra="forbid")
    mode: Literal["individual", "tv"] = "individual"


class TVInput(BaseModel):
    """The screen relays a signed key, never an arbitrary remote ID, balance or wager."""
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=2000)
    round: int = Field(ge=1, strict=True)
    phase: Literal["betting", "playing", "results"]


class TVManage(BaseModel):
    """Only the owning TV may explicitly remove a seat or end its whole table."""
    model_config = ConfigDict(extra="forbid")
    action: Literal["remove", "close"]
    seat: str = Field(default="", max_length=32)
    revision: int = Field(ge=1, strict=True)


def _tv_identity(request, conn, user):
    """Check the live screen pairing and its owner even when someone has signed in on the TV."""
    screen = screen_device(request, conn)
    if screen["user_id"] != user["id"] or screen["speaker"]:
        raise HTTPException(403, "Use this TV as its owner to host remote players.")
    return screen, casino_remote.actor("screen", screen)


class TableMove(BaseModel):
    """Only player intentions enter the rules engine; cards and payouts never do."""
    model_config = ConfigDict(extra="forbid")
    action: Literal["join", "bet", "start", "hit", "stand", "leave", "refill"]
    revision: int | None = Field(default=None, ge=1, strict=True)
    amount: int = Field(default=0, ge=0, le=500, strict=True)
    choice: str = Field(default="", max_length=12)


def _table_identity(request, conn, user):
    """Give each paired TV its own seat, while signed-in browsers play as their account."""
    if _session_user(request, conn) is None:
        screen = _screen(request, conn)
        if screen is not None:
            return f"screen:{screen['id']}:{user['id']}", screen["name"]
    return f"user:{user['id']}", user["username"]


def _casino_game(slug):
    """Keep table endpoints scoped to the four multiplayer registry entries."""
    if slug not in casino.GAMES:
        raise HTTPException(404, "No multiplayer tables for this game")


def _casino_call(fn, *args, **kwargs):
    """Translate a rules refusal into a small client-visible API error."""
    try:
        return fn(*args, **kwargs)
    except casino.Refused as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("/api/media/games/{slug}/rooms")
def table_list(slug: str, request: Request, user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
    actor, _ = _table_identity(request, conn, user)
    paired = _screen(request, conn)
    display = None
    if paired is not None and not paired["speaker"] and paired["user_id"] == user["id"] and screens.may_use(conn, user, request.app.state.directory):
        display = casino_remote.actor("screen", paired)  # A fresh tab can resume its TV without consuming a player seat.
    return {"rooms": casino.rooms(conn, slug, actor, display)}


@router.post("/api/media/games/{slug}/rooms")
def table_create(slug: str, request: Request, body: TableCreate | None = None,
                 user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
    if body is not None and body.mode == "tv":
        screen, actor = _tv_identity(request, conn, user)
        return _casino_call(casino.create, conn, slug, actor, screen["name"], couch=True)
    actor, name = _table_identity(request, conn, user)
    return _casino_call(casino.create, conn, slug, actor, name)


@router.get("/api/media/games/{slug}/rooms/{code}")
def table_state(slug: str, code: str, request: Request, user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
    actor, name = _table_identity(request, conn, user)
    return _casino_call(casino.room, conn, slug, code, actor, name)


@router.post("/api/media/games/{slug}/rooms/{code}")
def table_move(slug: str, code: str, body: TableMove, request: Request,
               user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
    actor, name = _table_identity(request, conn, user)
    return _casino_call(casino.room, conn, slug, code, actor, name, **body.model_dump())


@router.get("/api/media/games/{slug}/rooms/{code}/tv")
def tv_table(slug: str, code: str, request: Request, user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
    _, actor = _tv_identity(request, conn, user)
    return _casino_call(casino.couch, conn, slug, code, actor)


@router.post("/api/media/games/{slug}/rooms/{code}/tv/input")
def tv_input(slug: str, code: str, body: TVInput, request: Request, user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
    screen, display = _tv_identity(request, conn, user)
    try:
        proof = casino_remote.read(request.app.state.settings.secret_key, body.token)
    except casino_remote.BadData as exc:
        raise HTTPException(403, "That remote input expired or could not be verified. Press again.") from exc
    remote = remotes.get(conn, proof["remote"])
    if (remote is None or remote["user_id"] != screen["user_id"] or proof["screen"] != display
            or proof["pairing"] != casino_remote.pairing(remote)):
        raise HTTPException(403, "This remote is no longer paired to this TV's owner.")
    command = {"id": proof["id"], "action": proof["action"], "actor": casino_remote.actor("remote", remote), "name": remote["name"]}
    return _casino_call(casino.couch, conn, slug, code, display, command=command,
                        expected_round=body.round, expected_phase=body.phase)


@router.post("/api/media/games/{slug}/rooms/{code}/tv/manage")
def tv_manage(slug: str, code: str, body: TVManage, request: Request, user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
    _, actor = _tv_identity(request, conn, user)
    return _casino_call(casino.couch, conn, slug, code, actor, close=body.action == "close",
                        remove=body.seat if body.action == "remove" else None, revision=body.revision)
