"""Media → Games: the built-in games (mnemerynne/games.py). The list for Media's Games view, and each game's page,
which Media's game layer frames (media.js games). Both sit behind Media's own guard (media._media_on): Media on,
and the person (or the TV's owner) in the Media group."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from mnemerynne import casino, games
from mnemerynne.web.deps import _session_user, get_db
from mnemerynne.web.media import _media_on, _screen, media_user

def _uncached(response: Response):
    """A shared URL still represents this caller's seat and the current round, never a cached snapshot."""
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(dependencies=[Depends(_media_on), Depends(_uncached)])


@router.get("/api/media/games")
def media_games(user=Depends(media_user)):
    """The games Media → Games shows, for a signed-in person or a paired screen (the TV)."""
    return {"games": games.listing()}


@router.get("/media/games/{slug}", response_class=HTMLResponse)
def game_page(slug: str, request: Request, user=Depends(media_user)):
    """A game's page: its canvas and its scripts (static/games/<slug>/), in the person's theme. No `user` in the
    template, so the page carries none of the app's own scripts (messages, notifications) inside the game frame."""
    game = games.get(slug)
    if game is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such game")
    return request.app.state.templates.TemplateResponse(request, "game.html", {"game": game})


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
    return {"rooms": casino.rooms(conn, slug, actor)}


@router.post("/api/media/games/{slug}/rooms")
def table_create(slug: str, request: Request, user=Depends(media_user), conn=Depends(get_db)):
    _casino_game(slug)
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
