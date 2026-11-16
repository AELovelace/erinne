"""Retro netplay: up to four people playing one game together, each in their own browser (Doll, 2026-11-15).

The game runs in every browser (EmulatorJS), kept identical by lockstep (static/retro-netplay.js): each player's
buttons for a frame go to everyone else a few frames ahead, and every browser applies all of them at that same
frame, waiting for anyone who's behind. This module is only the meeting place and the relay: rooms live in the
media service's memory (it's one process: services/registry.py), and a room's messages go to everyone else in it,
tagged with who sent them. Nothing about a game's play is kept.

EmulatorJS has a netplay of its own, but its "control syncing" is broken by its own account (emulator.js:
`//control syncing - broken`): the host applies presses at once and the others ten frames later, so the games
drift apart. And its server would be another service, open to whoever reaches it. Here, only signed-in people who
can read the game on the share see its rooms or join them (web/retro.py checks, as for playing it).

A room:
  - is for one ROM (its file id, so the same share permissions), opened by its host, who is player 1 (slot 0);
    the next three to join are players 2-4, in the order they came;
  - closes when its host leaves (everyone is told), and is forgotten when nobody's left;
  - relays text messages (JSON: the lockstep's inputs, "ready", checksums) to everyone else with "from": slot,
    and the host's binary messages (a save state to start from) to everyone else, after a byte saying who;
  - lets only the host send what steers the room ("start", "state", "delay", "kick"); the server itself says
    "welcome", "players" and "closed".
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger("mnemerynne.media")

MAX_PLAYERS = 4                     # Retro's cores take four controllers
MAX_ROOMS = 64                      # open at once, all games together
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I: read out across a room
CODE_LENGTH = 5
MAX_TEXT = 4096                     # a relayed text message (inputs are a few dozen bytes)
MAX_BINARY = 16 << 20               # a save state (a PS1's is a few MB)
HOST_ONLY = {"start", "state", "delay", "kick"}   # what only the host may send
RELAYED = HOST_ONLY | {"i", "ready", "sum", "hello", "chat"}   # the message types passed on at all


class NetplayError(Exception):
    """A refusal; the message is safe to show."""


Send = Callable[[str | bytes], Awaitable[None]]


@dataclass
class Member:
    slot: int                        # 0 = host, player 1
    user_id: int
    name: str
    send: Send                       # to this person's browser (text or bytes)


@dataclass
class Room:
    code: str
    game_id: int                     # the ROM's file id
    title: str
    console: str
    host_name: str
    created: float = field(default_factory=time.time)
    members: dict[int, Member] = field(default_factory=dict)
    started: bool = False
    closed: bool = False

    def roster(self) -> list[dict]:
        return [{"slot": m.slot, "player": m.slot + 1, "name": m.name} for m in sorted(self.members.values(), key=lambda m: m.slot)]

    def summary(self) -> dict:
        """What the room list shows."""
        return {"code": self.code, "game_id": self.game_id, "title": self.title, "console": self.console,
                "host": self.host_name, "players": len(self.members), "max": MAX_PLAYERS, "started": self.started}


class Rooms:
    """Every open room (one per media service process)."""

    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}
        self.lock = asyncio.Lock()

    def _code(self) -> str:
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self.rooms:
                return code

    def open_for(self, game_ids) -> list[dict]:
        """The rooms open for these games (the ones the asker may read), newest first."""
        wanted = set(game_ids)
        return [r.summary() for r in sorted(self.rooms.values(), key=lambda r: -r.created)
                if r.game_id in wanted and not r.closed]

    def get(self, code: str) -> Room | None:
        return self.rooms.get((code or "").strip().upper())

    async def open(self, *, game_id: int, title: str, console: str, user_id: int, name: str, send: Send) -> tuple[Room, Member]:
        """A new room for a game, its host being this person."""
        async with self.lock:
            if len(self.rooms) >= MAX_ROOMS:
                raise NetplayError("There are too many rooms open right now. Try again in a bit.")
            room = Room(code=self._code(), game_id=game_id, title=title, console=console, host_name=name)
            host = Member(slot=0, user_id=user_id, name=name, send=send)
            room.members[0] = host
            self.rooms[room.code] = room
        await self._welcome(room, host)
        log.info("Retro netplay: %s opened room %s for %s", name, room.code, title)
        return room, host

    async def join(self, code: str, *, game_id: int, user_id: int, name: str, send: Send) -> tuple[Room, Member]:
        """Into a room for the same game, in its first free slot."""
        async with self.lock:
            room = self.get(code)
            if room is None or room.closed:
                raise NetplayError("That room has closed.")
            if room.game_id != game_id:
                raise NetplayError("That room is playing another game.")
            free = next((s for s in range(MAX_PLAYERS) if s not in room.members), None)
            if free is None:
                raise NetplayError("That room is full (four players).")
            member = Member(slot=free, user_id=user_id, name=name, send=send)
            room.members[free] = member
        await self._welcome(room, member)
        await self._roster(room)
        return room, member

    async def leave(self, room: Room, member: Member) -> None:
        """Someone left (closed the page, lost the connection). The host leaving closes the room."""
        async with self.lock:
            if room.members.get(member.slot) is not member:
                return
            del room.members[member.slot]
            if member.slot == 0:
                room.closed = True
            gone = room.closed or not room.members
            if gone:
                self.rooms.pop(room.code, None)
        if room.closed:
            await self._everyone(room, json.dumps({"t": "closed", "why": f"{member.name} closed the room."}))
        else:
            await self._roster(room)

    async def relay(self, room: Room, member: Member, message: str | bytes) -> None:
        """A message from `member` to everyone else in the room, checked first."""
        if room.closed:
            return
        if isinstance(message, bytes):
            if member.slot != 0 or len(message) > MAX_BINARY:
                return                                    # only the host sends a state, and not an endless one
            await self._everyone(room, bytes([member.slot]) + message, but=member.slot)
            return
        if len(message) > MAX_TEXT:
            return
        try:
            data = json.loads(message)
        except ValueError:
            return
        if isinstance(data, dict) and data.get("t") == "ping":   # keep-alive: answered, so no proxy (nginx's 60 s
            await self._safe(member, '{"t":"pong"}')              # read timeout) drops a quiet room's connection
            return
        if not isinstance(data, dict) or data.get("t") not in RELAYED:
            return
        if data["t"] in HOST_ONLY and member.slot != 0:
            return
        if data["t"] == "start":
            room.started = True
        if data["t"] == "kick":                            # the host lets someone go: they're told, then dropped
            target = room.members.get(data.get("slot")) if isinstance(data.get("slot"), int) else None
            if target is not None and target.slot != 0:
                await self._safe(target, json.dumps({"t": "closed", "why": "The host let you go."}))
            return
        data["from"] = member.slot
        await self._everyone(room, json.dumps(data, separators=(",", ":")), but=member.slot)

    async def _welcome(self, room: Room, member: Member) -> None:
        await self._safe(member, json.dumps({"t": "welcome", "code": room.code, "slot": member.slot,
                                             "players": room.roster(), "started": room.started}))

    async def _roster(self, room: Room) -> None:
        await self._everyone(room, json.dumps({"t": "players", "players": room.roster()}))

    async def _everyone(self, room: Room, message: str | bytes, but: int | None = None) -> None:
        for m in list(room.members.values()):
            if m.slot != but:
                await self._safe(m, message)

    @staticmethod
    async def _safe(member: Member, message: str | bytes) -> None:
        try:
            await member.send(message)
        except Exception:   # noqa: BLE001 - a browser that's gone; its own connection notices and leaves
            pass


ROOMS = Rooms()
