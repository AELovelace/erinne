"""Live delivery for Rooms: one server-sent-events stream per open tab (GET /api/rooms/stream).

Everything that changes a room is written to chat_events (rooms.event) and then wake() is called,
so a stream sends it straight away; each stream also looks at the table every POLL_SECONDS, so
events written by another process (the worker, a second web worker) still arrive. A stream sends
only events for rooms its person can see (checked again every VISIBLE_SECONDS, so leaving a group
cuts it off), and a client that reconnects with ?after=<last event id> gets what it missed.

Presence (who has a stream open, and when they were last active) and typing are not stored: they
go only to the streams in this process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from mnemerynne import db, rooms

log = logging.getLogger("mnemerynne.rooms")

POLL_SECONDS = 5.0
HEARTBEAT_SECONDS = 20.0
VISIBLE_SECONDS = 30.0
IDLE_SECONDS = 300.0
BATCH = 200


@dataclass(eq=False)
class Listener:
    user_id: int
    username: str
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    queue: list = field(default_factory=list)      # ephemeral events (typing, presence, read)


def sse(kind: str, payload: dict, event_id: int | None = None) -> bytes:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


class Bus:
    def __init__(self, db_path, clock: Callable[[], float] = time.monotonic):
        self.db_path = db_path
        self.clock = clock
        self.listeners: set[Listener] = set()
        self.active_at: dict[int, float] = {}           # user id -> last time they did something
        # The event loop the streams run on: routes that write run in worker threads, and asyncio
        # events may only be set from their own loop, so wake() and push() hand over to it.
        self.loop: asyncio.AbstractEventLoop | None = None

    def _on_loop(self, fn: Callable[[], None]) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            return                                      # no stream open in this process yet
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            fn()
        else:
            loop.call_soon_threadsafe(fn)

    # ------------------------------------------------------------ presence
    def online(self) -> set[int]:
        """People with a stream open now (for @here)."""
        return {lst.user_id for lst in self.listeners}

    def presence(self) -> dict[str, str]:
        """username -> "online" or "idle" (no activity for IDLE_SECONDS) for everyone with a stream open."""
        now = self.clock()
        out: dict[str, str] = {}
        for lst in self.listeners:
            idle = now - self.active_at.get(lst.user_id, now) > IDLE_SECONDS
            if out.get(lst.username) != "online":
                out[lst.username] = "idle" if idle else "online"
        return out

    def touch(self, user_id: int) -> None:
        was_idle = self.clock() - self.active_at.get(user_id, 0) > IDLE_SECONDS
        self.active_at[user_id] = self.clock()
        if was_idle:
            self._presence_changed()

    def _presence_changed(self) -> None:
        self.push(None, "presence", {"presence": self.presence()})

    # ------------------------------------------------------------- sending
    def wake(self) -> None:
        """New rows in chat_events: every stream looks now (from any thread)."""
        def go():
            for lst in list(self.listeners):
                lst.wake.set()
        self._on_loop(go)

    def push(self, user_ids: set[int] | None, kind: str, payload: dict) -> None:
        """An ephemeral event to these people's streams (None: everyone's), from any thread."""
        def go():
            for lst in list(self.listeners):
                if user_ids is None or lst.user_id in user_ids:
                    lst.queue.append((kind, payload))
                    lst.wake.set()
        self._on_loop(go)

    def latest(self) -> int:
        conn = db.connect(self.db_path)
        try:
            return conn.execute("SELECT coalesce(max(id), 0) FROM chat_events").fetchone()[0]
        finally:
            conn.close()

    # ------------------------------------------------------------ streaming
    def _fetch(self, user_id: int, after: int, directory, seen: dict[int, tuple[float, bool]]) -> list[tuple]:
        """Events after `after` in rooms this person can see: [(id, kind, payload)], plus the last id looked at."""
        conn = db.connect(self.db_path)
        try:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if user is None or not user["active"]:
                return [(None, "end", {})]
            if not rooms.enabled(conn):
                return [(None, "end", {"reason": "Rooms was turned off"})]
            rows = conn.execute("SELECT id, room_id, kind, payload FROM chat_events WHERE id > ? ORDER BY id LIMIT ?",
                                (after, BATCH)).fetchall()
            out = []
            now = self.clock()
            for row in rows:
                cached = seen.get(row["room_id"])
                if cached is None or now - cached[0] > VISIBLE_SECONDS:
                    try:
                        ok = rooms.can_see(conn, user, rooms.room(conn, row["room_id"]), directory)
                    except rooms.NotFound:
                        ok = False
                    cached = seen[row["room_id"]] = (now, ok)
                if cached[1]:
                    out.append((row["id"], row["kind"], json.loads(row["payload"])))
                else:
                    out.append((row["id"], None, None))     # moves the cursor without sending
            return out
        finally:
            conn.close()

    async def stream(self, user: sqlite3.Row, after: int, directory, disconnected: Callable) -> AsyncIterator[bytes]:
        self.loop = asyncio.get_running_loop()
        lst = Listener(user["id"], user["username"])
        first = not any(x.user_id == user["id"] for x in self.listeners)
        self.listeners.add(lst)
        self.active_at[user["id"]] = self.clock()
        if first:
            self._presence_changed()
        cursor = after if after > 0 else await asyncio.to_thread(self.latest)
        seen: dict[int, tuple[float, bool]] = {}
        last_beat = self.clock()
        try:
            yield sse("hello", {"cursor": cursor, "presence": self.presence()})
            while True:
                while lst.queue:
                    kind, payload = lst.queue.pop(0)
                    yield sse(kind, payload)
                fetched = await asyncio.to_thread(self._fetch, user["id"], cursor, directory, seen)
                for event_id, kind, payload in fetched:
                    if kind == "end":
                        yield sse("end", {"reason": payload.get("reason") or "signed out"})
                        return
                    cursor = event_id
                    if kind is not None:
                        yield sse(kind, payload, event_id)
                if len(fetched) >= BATCH:
                    continue                                # more waiting: don't sleep
                if await disconnected():
                    return
                lst.wake.clear()
                if lst.queue:
                    continue
                try:
                    await asyncio.wait_for(lst.wake.wait(), timeout=POLL_SECONDS)
                except asyncio.TimeoutError:
                    pass
                if self.clock() - last_beat >= HEARTBEAT_SECONDS:
                    last_beat = self.clock()
                    yield b": ping\n\n"
        finally:
            self.listeners.discard(lst)
            if not any(x.user_id == user["id"] for x in self.listeners):
                self._presence_changed()
