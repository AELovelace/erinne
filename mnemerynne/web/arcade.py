"""The main origin's side of the arcade (mnemerynne/arcade.py): a ticket into it for someone who may play, and
the admin's Games settings (Settings → Entertainment → Games)."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from mnemerynne import arcade, group_refs
from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import admin_user, api_user, get_db, get_settings, is_admin

router = APIRouter()
admin_router = APIRouter()


class TicketBody(BaseModel):
    path: str = Field("/", max_length=500)   # the page on the games site to open, e.g. /lumibot/


@router.post("/api/arcade/ticket")
def ticket(body: TicketBody, request: Request, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db),
           settings: Settings = Depends(get_settings)):
    """A one-use, one-minute address that signs this person into the arcade at `path`."""
    where = arcade.origin(settings)
    if not where:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The arcade isn't set up on this server (MNEMERYNNE_ARCADE_URL).")
    try:
        path = arcade.clean_path(body.path)
    except arcade.ArcadeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    if not arcade.may_play(conn, user, request.app.state.directory, is_admin(request, conn, user)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Games aren't on for you.")
    token = arcade.make_ticket(request.app.state.arcade.secret_key, user, path)
    return {"url": f"{where}{arcade.OWN_PREFIX}enter?t={token}", "expires_in": arcade.TICKET_SECONDS}


# -------------------------------------------------------------------- admin
def _status(conn: sqlite3.Connection, settings: Settings) -> dict:
    cfg = arcade.load_settings(conn)
    return {"settings": cfg | {"group": group_refs.label(conn, cfg["group"])},
            "arcade_url": arcade.origin(settings)}


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
