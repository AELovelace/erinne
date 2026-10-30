"""Media → Games: the built-in games (mnemerynne/games.py). The list for Media's Games view, and each game's page,
which Media's game layer frames (media.js games). Both sit behind Media's own guard (media._media_on): Media on,
and the person (or the TV's owner) in the Media group."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from mnemerynne import games
from mnemerynne.web.media import _media_on, media_user

router = APIRouter(dependencies=[Depends(_media_on)])


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
