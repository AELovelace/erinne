"""Screens: a browser showing Media (the TV) that its owner casts to from another device (the phone).

Pairing: the owner, signed in on the TV, names it; the TV gets a screen key in a long-lived cookie
(only its hash is stored). The key opens Media, and nothing else, as its owner, so the TV keeps
working after sign-in sessions expire; removing the screen revokes it.

Casting: the phone posts a command (play this film, album, station, live source or YouTube link;
pause, seek, volume, next, stop) for one of its owner's screens. The web app hands it to the screen's
open event stream (Hub, in memory: this is one uvicorn process), the screen's page acts on it and
reports what it is playing back every few seconds, which is what the phone's remote shows.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import sqlite3
import time

COOKIE = "mnemerynne_screen"
COOKIE_DAYS = 400               # browsers cap cookies here; it's renewed whenever the screen connects
MAX_PER_USER = 10
STATE_SECONDS = 15              # a screen silent this long counts as gone from the remote's point of view


class ScreenError(ValueError):
    """A refusal; the message is safe to show."""
    status = 422


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name or len(name) > 40:
        raise ScreenError("Name the screen (up to 40 characters), e.g. Living room.")
    return name


def create(conn: sqlite3.Connection, user_id: int, name: str) -> tuple[int, str]:
    """(screen id, key) in the caller's transaction; a screen of the same name is replaced (re-paired)."""
    name = clean_name(name)
    conn.execute("DELETE FROM media_screens WHERE user_id = ? AND lower(name) = lower(?)", (user_id, name))
    if conn.execute("SELECT count(*) FROM media_screens WHERE user_id = ?", (user_id,)).fetchone()[0] >= MAX_PER_USER:
        raise ScreenError(f"At most {MAX_PER_USER} screens; remove one first.")
    token = secrets.token_urlsafe(32)
    screen_id = conn.execute("INSERT INTO media_screens(user_id, name, token_hash) VALUES (?, ?, ?)",
                             (user_id, name, _hash(token))).lastrowid
    return screen_id, token


def by_token(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    """The screen a key belongs to, with its owner (active accounts only)."""
    if not token or len(token) > 200:
        return None
    return conn.execute("SELECT s.*, u.username FROM media_screens s JOIN users u ON u.id = s.user_id "
                        "WHERE s.token_hash = ? AND u.active = 1", (_hash(token),)).fetchone()


def owned(conn: sqlite3.Connection, user_id: int, screen_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM media_screens WHERE id = ? AND user_id = ?", (screen_id, user_id)).fetchone()


def of_user(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM media_screens WHERE user_id = ? ORDER BY name COLLATE NOCASE",
                        (user_id,)).fetchall()


def seen(conn: sqlite3.Connection, screen_id: int) -> None:
    conn.execute("UPDATE media_screens SET last_seen_at = datetime('now') WHERE id = ?", (screen_id,))


class Hub:
    """Open screen streams and what each screen last said it was playing. Used from the event loop only."""

    def __init__(self) -> None:
        self._listeners: dict[int, set[asyncio.Queue]] = {}
        self._state: dict[int, tuple[float, dict]] = {}

    def listen(self, screen_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._listeners.setdefault(screen_id, set()).add(queue)
        return queue

    def unlisten(self, screen_id: int, queue: asyncio.Queue) -> None:
        listeners = self._listeners.get(screen_id)
        if listeners is not None:
            listeners.discard(queue)
            if not listeners:
                del self._listeners[screen_id]

    def online(self, screen_id: int) -> bool:
        return bool(self._listeners.get(screen_id))

    def push(self, screen_id: int, command: dict) -> int:
        """Hand a command to every open stream of the screen; how many took it."""
        sent = 0
        for queue in list(self._listeners.get(screen_id, ())):
            try:
                queue.put_nowait(command)
                sent += 1
            except asyncio.QueueFull:   # a stuck tab: it gets the next one
                pass
        return sent

    def close(self, screen_id: int) -> None:
        """The screen was removed: end its streams."""
        self.push(screen_id, {"action": "unpaired"})

    def set_state(self, screen_id: int, state: dict) -> None:
        self._state[screen_id] = (time.monotonic(), state)

    def state(self, screen_id: int) -> dict | None:
        hit = self._state.get(screen_id)
        if hit is None or time.monotonic() - hit[0] > STATE_SECONDS:
            return None
        return hit[1]
