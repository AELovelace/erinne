"""Visiting each other's flats (Lumi Pet room mode, docs/plans/lumi-pet-social.md phase 3, 2026-11-19).

Someone's flat is their Lumi Pet room mode (kitchen, bedroom, den), drawn from their own save (lumipet.house_view)
for a visitor whose own Lumi walks around in it. Things there are look-only for a visitor: nothing a visitor does
touches the host's save.

Who may come in is the host's front door (lumipet.door): "open" (anyone signed in walks straight in), "knock" (the
default: the host, who must be home, lets them in or not) or "closed"; and nobody on the host's block list, ever.
Letting someone in gives them a PASS for PASS_SECONDS, so they can wander between rooms and pop back. Turning the door
to closed, or blocking someone, sends guests home at once (send_home), and their passes go with them.

All in memory, like Lumi Town (lumitown.py): who's in which flat and where they stand (`here`, refreshed by each page
every few seconds and as its Lumi walks), knocks waiting for an answer (KNOCK_SECONDS), and passes. A restart just
means a moment until everyone has said where they are again, and anyone let in knocks again. The host is "home" while
their own Lumi Pet room mode is open (their page reports itself in its own flat, like a guest does).

Pages hear what happens on the event bus (events.py, lumitown.CHANNEL), through the same stream as the town:
{"kind": "walk"} (someone moved in a flat's room: their place, x, y, facing), {"kind": "moved"} (someone arrived or left),
{"kind": "knock", "host"} (for the host), {"kind": "answer", "guest", "yes"} (for the knocker) and
{"kind": "home", "out": [ids], "host"} (those guests must go). A TV can't use the stream, so the same news rides on
its regular POST (the knocks waiting for a host, a knock's answer, and a refusal once the door has shut)."""

from __future__ import annotations

import secrets
import threading
import time

SEEN_SECONDS = 20          # a page that stopped saying where it is has left
KNOCK_SECONDS = 60         # a knock nobody answers is forgotten after this
PASS_SECONDS = 3 * 3600    # someone let in may stay, or come back, for three hours
MAX_PEOPLE = 200           # guards: never more than this many remembered at once
MAX_KNOCKS = 200
ROOMS = ("kitchen", "bedroom", "den")   # room-ui.js ROOMS: where someone can stand in a flat


def place(host: int, room: str) -> str:
    """Where a line said in a flat is heard: one room of one flat."""
    return f"house:{host}:{room}"


class Houses:
    """Who is in which flat, knocks, and passes."""

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.lock = threading.Lock()
        self.people: dict[int, dict] = {}            # user → {host, room, x, y, face, seen}
        self.knocks: dict[str, dict] = {}            # id → {id, guest, host, at, answer (None, True or False)}
        self.passes: dict[tuple[int, int], float] = {}   # (host, guest) → until

    def _sweep(self, now: float) -> None:
        self.people = {uid: p for uid, p in self.people.items() if now - p["seen"] < SEEN_SECONDS}
        self.knocks = {k: v for k, v in self.knocks.items() if now - v["at"] < KNOCK_SECONDS}
        self.passes = {k: until for k, until in self.passes.items() if until > now}

    # ---- Who's where.
    def visit(self, user_id: int, host: int, room: str, x: float, y: float, face: int) -> bool:
        """This person's Lumi stands at x, y in a room of `host`'s flat, now. True when that's somewhere new (arrived,
        or another room), so the others hear of it at once."""
        with self.lock:
            now = self.clock()
            self._sweep(now)
            was = self.people.get(user_id)
            if was is None and len(self.people) >= MAX_PEOPLE:
                return False
            self.people[user_id] = {"host": host, "room": room, "x": x, "y": y, "face": face, "seen": now}
            return was is None or (was["host"], was["room"]) != (host, room)

    def leave(self, user_id: int) -> bool:
        with self.lock:
            return self.people.pop(user_id, None) is not None

    def where(self, user_id: int) -> str | None:
        """The place (one room of one flat) this person is in now, or None."""
        with self.lock:
            self._sweep(self.clock())
            p = self.people.get(user_id)
            return place(p["host"], p["room"]) if p else None

    def seen_at(self, user_id: int) -> float | None:
        """When this person last said where they are in a flat (their clock), or None."""
        with self.lock:
            self._sweep(self.clock())
            p = self.people.get(user_id)
            return p["seen"] if p else None

    def inside(self, host: int, but: int | None = None) -> list[dict]:
        """Everyone in `host`'s flat now (the host too, if home), except `but`: [{id, room, x, y, face}]."""
        with self.lock:
            self._sweep(self.clock())
            return [{"id": uid, "room": p["room"], "x": p["x"], "y": p["y"], "face": p["face"]}
                    for uid, p in self.people.items() if p["host"] == host and uid != but]

    def home(self, host: int) -> bool:
        """Whether the host is in their own flat now (their Lumi Pet room mode is open)."""
        with self.lock:
            self._sweep(self.clock())
            p = self.people.get(host)
            return bool(p) and p["host"] == host

    # ---- Knocks and passes.
    def knock(self, guest: int, host: int) -> dict:
        """A knock on `host`'s door (one at a time per guest and door: a second knock is the same one)."""
        with self.lock:
            now = self.clock()
            self._sweep(now)
            for k in self.knocks.values():
                if k["guest"] == guest and k["host"] == host and k["answer"] is None:
                    return dict(k)
            if len(self.knocks) >= MAX_KNOCKS:
                raise ValueError("The town is very busy. Try knocking again in a moment.")
            k = {"id": secrets.token_hex(6), "guest": guest, "host": host, "at": now, "answer": None}
            self.knocks[k["id"]] = k
            return dict(k)

    def knock_of(self, knock_id: str) -> dict | None:
        with self.lock:
            self._sweep(self.clock())
            k = self.knocks.get(knock_id)
            return dict(k) if k else None

    def waiting(self, host: int) -> list[dict]:
        """The knocks on `host`'s door still waiting for an answer, oldest first."""
        with self.lock:
            self._sweep(self.clock())
            return sorted((dict(k) for k in self.knocks.values() if k["host"] == host and k["answer"] is None), key=lambda k: k["at"])

    def answer(self, knock_id: str, host: int, yes: bool) -> dict:
        """The host answers a knock on their own door. Yes gives the guest a pass. Raises LookupError if there's no such knock."""
        with self.lock:
            now = self.clock()
            self._sweep(now)
            k = self.knocks.get(knock_id)
            if k is None or k["host"] != host:
                raise LookupError("That knock has gone: they may have wandered off.")
            k["answer"] = bool(yes)
            if yes:
                self.passes[(host, k["guest"])] = now + PASS_SECONDS
            return dict(k)

    def let_in(self, host: int, guest: int) -> bool:
        """Whether the host has let this guest in lately (an unexpired pass)."""
        with self.lock:
            self._sweep(self.clock())
            return (host, guest) in self.passes

    def send_home(self, host: int, guest: int | None = None) -> list[int]:
        """The door shut (guest None: everyone) or one guest blocked: their passes and knocks go, and those in the flat
        leave it. Returns who was in the flat and must go (their pages hear a "home" event)."""
        with self.lock:
            self._sweep(self.clock())
            gone = [uid for uid, p in self.people.items() if p["host"] == host and uid != host and guest in (None, uid)]
            for uid in gone:
                del self.people[uid]
            self.passes = {k: v for k, v in self.passes.items() if not (k[0] == host and guest in (None, k[1]))}
            self.knocks = {k: v for k, v in self.knocks.items() if not (v["host"] == host and guest in (None, v["guest"]))}
            return gone


def access(door: str, blocked: list[int], host: int, guest: int, passes: Houses) -> str:
    """May `guest` come into `host`'s flat? "own" (it's theirs), "in" (open door, or let in), "knock" (they may knock),
    or "closed" (a closed door, or blocked: a blocked person is only ever told the door is closed)."""
    if guest == host:
        return "own"
    if guest in blocked or door == "closed":
        return "closed"
    if door == "open" or passes.let_in(host, guest):
        return "in"
    return "knock"


HOUSES = Houses()
