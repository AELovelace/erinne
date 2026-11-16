"""Who is in Lumi Town right now (2026-11-14): each person's Lumi shows on the street, or in the place, where they are,
for everyone else in town (static/games/lumi-pet/game.js townHere, drawFolk).

The town page tells the server where its Lumi stands every few seconds (POST /api/media/games/lumi-pet/town); the
answer is everyone else seen within SEEN_SECONDS. In memory, like the game lobbies' presence (lobby.py): one web
process, and a restart just means a few seconds until everyone has said where they are again. Someone who turned
"Show me in town" off (the pet save's town_seen) is never recorded, and is forgotten at once."""

from __future__ import annotations

import threading
import time

SEEN_SECONDS = 20          # a page that stopped asking (closed, hidden, gone to a game) drops out after this
MAX_PEOPLE = 200           # a guard: never more than this many remembered at once


class Town:
    """Where each person was last seen: {user_id: {"at", "street", "seen"}}."""

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.lock = threading.Lock()
        self.people: dict[int, dict] = {}

    def _sweep(self, now: float) -> None:
        self.people = {uid: p for uid, p in self.people.items() if now - p["seen"] < SEEN_SECONDS}

    def visit(self, user_id: int, at: str, street: str) -> None:
        """This person's Lumi is at `at` (a place id) on `street`, now."""
        with self.lock:
            now = self.clock()
            self._sweep(now)
            if user_id not in self.people and len(self.people) >= MAX_PEOPLE:
                return
            self.people[user_id] = {"at": at, "street": street, "seen": now}

    def leave(self, user_id: int) -> None:
        with self.lock:
            self.people.pop(user_id, None)

    def here(self, but: int | None = None) -> list[tuple[int, str, str]]:
        """Everyone seen lately, except `but`: [(user_id, at, street)], the most recently seen first."""
        with self.lock:
            now = self.clock()
            self._sweep(now)
            found = sorted(((uid, p) for uid, p in self.people.items() if uid != but), key=lambda item: -item[1]["seen"])
            return [(uid, p["at"], p["street"]) for uid, p in found]


TOWN = Town()
