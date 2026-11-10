"""What every multiplayer table shares: who is still there, and table codes.

Casino tables (casino.py) and arcade showdowns (showdown.py) both keep their tables in the database, but whether a
player is still at one is not worth a database write: it lives here, in memory. The web app is one process (uvicorn
without workers), so every request sees the same record. After a restart nobody has been heard from yet, so everybody
is treated as just arrived and gets the full grace period to come back.

A player counts as there while:
- a live stream for the table is open on their page (showdowns), or
- their page asked about the table recently (casino tables poll every second or two).

How long "recently" is belongs to each kind of table: a casino seat is kept two minutes, so Back to Games and a quick
look elsewhere don't lose it; a showdown seat 30 seconds, because a race can't wait for someone who closed the game.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
import time


class Presence:
    """When each player was last heard from at each table, and which of them hold a stream open right now."""

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self._seen: dict[tuple, float] = {}                # (table, actor) -> last time heard from
        self._streams: dict[tuple, int] = {}               # (table, actor) -> streams open
        self._lock = threading.Lock()

    def seen(self, table: tuple, actor: str) -> None:
        """This player just asked about this table, or did something at it."""
        with self._lock:
            self._seen[(table, actor)] = self.clock()

    def opened(self, table: tuple, actor: str) -> None:
        """A stream for this table opened on the player's page: they count as there until it closes."""
        with self._lock:
            self._streams[(table, actor)] = self._streams.get((table, actor), 0) + 1
            self._seen[(table, actor)] = self.clock()

    def closed(self, table: tuple, actor: str) -> None:
        """That stream ended (the game closed, the page reloaded, the network dropped). The grace period starts now."""
        with self._lock:
            left = self._streams.get((table, actor), 0) - 1
            if left > 0:
                self._streams[(table, actor)] = left
            else:
                self._streams.pop((table, actor), None)
            self._seen[(table, actor)] = self.clock()

    def streaming(self, table: tuple, actor: str) -> bool:
        """Whether a stream for this table is open on this player's page right now."""
        with self._lock:
            return self._streams.get((table, actor), 0) > 0

    def quiet_for(self, table: tuple, actor: str) -> float:
        """Seconds since this player was last heard from at this table; 0 while a stream is open. Someone this process
        has never heard of (a seat saved before a restart) is taken as here now, so they get the whole grace period."""
        key = (table, actor)
        with self._lock:
            if self._streams.get(key, 0) > 0:
                return 0.0
            now = self.clock()
            if key not in self._seen:
                self._seen[key] = now
            return now - self._seen[key]

    def forget(self, table: tuple) -> None:
        """The table is gone: drop everything about it."""
        with self._lock:
            for key in [k for k in self._seen if k[0] == table]:
                del self._seen[key]
            for key in [k for k in self._streams if k[0] == table]:
                del self._streams[key]


PRESENCE = Presence()     # the one record for this process: casino.py and showdown.py both use it


def new_code(conn: sqlite3.Connection, table: str) -> str:
    """A fresh eight-character table code, unused in `table` (casino_rooms or arcade_rooms)."""
    while True:
        code = secrets.token_hex(4).upper()
        if conn.execute(f"SELECT 1 FROM {table} WHERE code = ?", (code,)).fetchone() is None:
            return code
