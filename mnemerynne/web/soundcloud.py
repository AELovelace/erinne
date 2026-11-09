"""Media → SoundCloud: which profile this browser shows, that profile's lists, and where a track plays from
(mnemerynne/media/soundcloud.py holds the key and talks to SoundCloud).

A paired screen shows the profile set for it (Kiosk settings, or its row in Media → Screens), else its owner's. Anyone
else, on a phone or a computer, shows the profile they saved themselves.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from mnemerynne.config import Settings
from mnemerynne.db import transaction
from mnemerynne.media import soundcloud
from mnemerynne.web.admin import _log
from mnemerynne.web.deps import _session_user, api_user, get_db, get_settings
from mnemerynne.web.media import _castable, _media_on, _screen, media_user, screens_user

router = APIRouter(dependencies=[Depends(_media_on)])

Tab = Literal["tracks", "albums", "playlists", "likes", "liked_sets", "reposts"]
SearchKind = Literal["tracks", "playlists", "users"]


def _refused(exc: soundcloud.SoundCloudError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _acting_screen(request: Request, conn: sqlite3.Connection, asked: bool) -> sqlite3.Row | None:
    """The screen this request speaks for. A computer can be paired and signed in at once: then only the TV page, which
    says screen=1, is the screen; Media opened normally there is the person."""
    screen = _screen(request, conn)
    if screen is None or not (asked or _session_user(request, conn) is None):
        return None
    return screen


def _which(request: Request, conn: sqlite3.Connection, user, asked: bool) -> tuple[str, str, bool]:
    """(profile name or "", whose it is, whether this browser may change it)."""
    screen = _acting_screen(request, conn, asked)
    if screen is not None and screen["soundcloud_profile"]:
        return screen["soundcloud_profile"], "screen", False
    source = "owner" if screen is not None else "own"
    return user["soundcloud_profile"] or "", source if user["soundcloud_profile"] else "none", screen is None


@router.get("/api/media/soundcloud/profile")
def get_profile(request: Request, screen: bool = False, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    """What the tab opens on. A profile SoundCloud can't find comes back as `error`, so its owner can fix it right there."""
    name, source, editable = _which(request, conn, user, screen)
    creds = soundcloud.credentials(conn, settings)
    answer = {"configured": creds is not None, "profile": name, "source": source, "editable": editable, "user": None, "error": None}
    if creds is not None and name:
        try:
            answer["user"] = soundcloud.profile(creds, name)
        except soundcloud.SoundCloudError as exc:
            answer["error"] = str(exc)
    return answer


class ProfileBody(BaseModel):
    profile: str = Field(..., max_length=300)


@router.put("/api/media/soundcloud/profile")
def save_profile(body: ProfileBody, user=Depends(api_user), conn: sqlite3.Connection = Depends(get_db)):
    """The signed-in person's own profile; empty clears it."""
    try:
        name = soundcloud.clean_profile(body.profile)
    except soundcloud.SoundCloudError as exc:
        raise _refused(exc) from None
    with transaction(conn):
        conn.execute("UPDATE users SET soundcloud_profile = ? WHERE id = ?", (name, user["id"]))
    return {"profile": name}


@router.get("/api/media/soundcloud/list/{tab}")
def list_tab(tab: Tab, request: Request, offset: int = Query(0, ge=0, le=soundcloud.MAX_ITEMS), screen: bool = False,
             account: str | None = Query(None, max_length=100), user=Depends(media_user),
             conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """A tab of the profile this browser shows, or of `account`, a profile opened from search."""
    if account is not None and not soundcloud.USER_URN.fullmatch(account):    # goes into an API path
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    name = "" if account is not None else _which(request, conn, user, screen)[0]
    if account is None and not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No SoundCloud profile is set here yet.")
    try:
        creds = soundcloud.need_credentials(conn, settings)
        return soundcloud.listing(creds, account or soundcloud.profile(creds, name)["urn"], tab, offset)
    except soundcloud.SoundCloudError as exc:
        raise _refused(exc) from None


@router.get("/api/media/soundcloud/search/{kind}")
def search(kind: SearchKind, q: str = Query("", max_length=300), offset: int = Query(0, ge=0, le=soundcloud.MAX_ITEMS),
           user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Tracks, playlists or profiles matching what someone typed; needs no saved profile."""
    try:
        return soundcloud.search(soundcloud.need_credentials(conn, settings), kind, q, offset)
    except soundcloud.SoundCloudError as exc:
        raise _refused(exc) from None


@router.get("/api/media/soundcloud/user/{urn}")
def get_user(urn: str, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db),
             settings: Settings = Depends(get_settings)):
    """The name and link of a profile opened from search."""
    if not soundcloud.USER_URN.fullmatch(urn):                 # goes into an API path
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        return soundcloud.person(soundcloud.need_credentials(conn, settings), urn)
    except soundcloud.SoundCloudError as exc:
        raise _refused(exc) from None


@router.get("/api/media/soundcloud/station/{urn}")
def get_station(urn: str, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    """Tracks related to one track: where a station starts, and how it tops itself up as it plays."""
    if not soundcloud.TRACK_URN.fullmatch(urn):                # goes into an API path
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        return soundcloud.related(soundcloud.need_credentials(conn, settings), urn)
    except soundcloud.SoundCloudError as exc:
        raise _refused(exc) from None


@router.get("/api/media/soundcloud/set/{urn}")
def get_set(urn: str, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db),
            settings: Settings = Depends(get_settings)):
    if not soundcloud.SET_URN.fullmatch(urn):                 # goes into an API path
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        return soundcloud.set_detail(soundcloud.need_credentials(conn, settings), urn)
    except soundcloud.SoundCloudError as exc:
        raise _refused(exc) from None


class StreamBody(BaseModel):
    id: str = Field(..., max_length=100, pattern=soundcloud.TRACK_URN.pattern)
    fresh: bool = False                                  # the last link failed in the browser: don't hand it back


@router.post("/api/media/soundcloud/stream")
def track_stream(body: StreamBody, user=Depends(media_user), conn: sqlite3.Connection = Depends(get_db),
                 settings: Settings = Depends(get_settings)):
    """Where a track plays from. Only asked as it starts, since SoundCloud limits these per day."""
    try:
        return soundcloud.stream(soundcloud.need_credentials(conn, settings), body.id, body.fresh)
    except soundcloud.SoundCloudError as exc:
        raise _refused(exc) from None


@router.put("/api/media/screens/{screen_id}/soundcloud")
def set_screen_profile(screen_id: int, body: ProfileBody, request: Request, user=Depends(screens_user),
                       conn: sqlite3.Connection = Depends(get_db)):
    """The profile a screen shows (Media → Screens; on a box, Kiosk settings as well). Empty: its owner's."""
    row = _castable(request, conn, user, screen_id)
    try:
        name = soundcloud.clean_profile(body.profile)
    except soundcloud.SoundCloudError as exc:
        raise _refused(exc) from None
    with transaction(conn):
        conn.execute("UPDATE media_screens SET soundcloud_profile = ? WHERE id = ?", (name, row["id"]))
        if row["user_id"] != user["id"]:
            _log(conn, user, "screen.soundcloud", row["name"], name or "cleared")
    return {"profile": name}
