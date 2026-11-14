"""Live delivery for Rooms: one server-sent-events stream per open tab (GET /api/rooms/stream).

Everything that changes a room is written to chat_events (rooms.event) and then wake() is called,
so a stream sends it straight away; each stream also looks at the table every POLL_SECONDS, so
events written by another process (the worker, a second web worker) still arrive. A stream sends
only events for rooms its person can see (checked again every VISIBLE_SECONDS, so leaving a group
cuts it off), and a client that reconnects with ?after=<last event id> gets what it missed.

Presence (who has a stream open, and when they were last active) and typing are not stored. Across
processes (docs/plans/services-rewrite.md, Phase 3: the rooms service, the web app and the worker each have
their own Bus) they travel on the event bus (mnemerynne/events.py): wake() and push() also publish on
"rooms:wake" / "rooms:push", listen() hands what other processes publish to this one's streams, and each
open stream keeps a "rooms:online:<user id>" key alive, so @here and the presence list count everyone's
streams, not only this process's. With Valkey off (one process), that is this process alone, as before;
the poll still brings every new event within POLL_SECONDS whatever happens to the bus.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from mnemerynne import db, events, rooms

log = logging.getLogger("mnemerynne.rooms")

POLL_SECONDS = 5.0
HEARTBEAT_SECONDS = 20.0
VISIBLE_SECONDS = 30.0
IDLE_SECONDS = 300.0
BATCH = 200
ONLINE_SECONDS = 3 * HEARTBEAT_SECONDS      # a stream's "rooms:online:<id>" key outlives one missed heartbeat


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
        self.id = secrets.token_hex(6)                  # this process's Bus: it ignores its own published events

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
        """People with a stream open now (for @here): here, and in every other process (events' keys)."""
        here = {lst.user_id for lst in self.listeners}
        return here | {int(user_id) for user_id, _ in self._elsewhere()}

    def _elsewhere(self) -> list[tuple[str, str]]:
        """(user id, username) for streams open in any process, from their keys; nothing if the bus can't say."""
        try:
            keys = events.expiring_keys("rooms:online:*")
            return [(key.rsplit(":", 1)[1], events.expiring_get(key) or "") for key in keys]
        except Exception:                               # Valkey gone: this process's streams only
            log.warning("Rooms: the event bus isn't answering; presence is this process's only.", exc_info=True)
            return []

    def _still_here(self, user_id: int, username: str) -> None:
        try:
            events.expiring_set(f"rooms:online:{user_id}", username, ONLINE_SECONDS)
        except Exception:
            pass

    def presence(self) -> dict[str, str]:
        """username -> "online" or "idle" (no activity for IDLE_SECONDS) for everyone with a stream open."""
        now = self.clock()
        out: dict[str, str] = {}
        for lst in self.listeners:
            idle = now - self.active_at.get(lst.user_id, now) > IDLE_SECONDS
            if out.get(lst.username) != "online":
                out[lst.username] = "idle" if idle else "online"
        for _, username in self._elsewhere():           # streams in other processes (idle is only known there)
            if username:
                out.setdefault(username, "online")
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
        """New rows in chat_events: every stream looks now, in every process (from any thread)."""
        self._wake_here()
        self._publish("rooms:wake", {})

    def _wake_here(self) -> None:
        def go():
            for lst in list(self.listeners):
                lst.wake.set()
        self._on_loop(go)

    def push(self, user_ids: set[int] | None, kind: str, payload: dict) -> None:
        """An ephemeral event to these people's streams (None: everyone's), in every process, from any thread."""
        self._push_here(user_ids, kind, payload)
        self._publish("rooms:push", {"users": sorted(user_ids) if user_ids is not None else None,
                                     "kind": kind, "payload": payload})

    def _push_here(self, user_ids: set[int] | None, kind: str, payload: dict) -> None:
        def go():
            for lst in list(self.listeners):
                if user_ids is None or lst.user_id in user_ids:
                    lst.queue.append((kind, payload))
                    lst.wake.set()
        self._on_loop(go)

    def _publish(self, channel: str, data: dict) -> None:
        try:
            events.publish_sync(channel, {**data, "from": self.id})
        except Exception:                               # Valkey gone: the poll still brings the events here
            log.warning("Rooms: couldn't tell the other processes (%s).", channel, exc_info=True)

    async def listen(self) -> None:
        """Run for as long as the process does (a background task): other processes' wakes and pushes, to the
        streams open here. A broken bus is retried every few seconds; the poll covers the gap."""
        self.loop = asyncio.get_running_loop()
        while True:
            try:
                async for channel, data in events.subscribe("rooms:wake", "rooms:push"):
                    if data.get("from") == self.id:
                        continue                        # our own: already delivered here
                    if channel == "rooms:wake":
                        self._wake_here()
                    else:
                        users = data.get("users")
                        self._push_here(set(users) if users is not None else None, data.get("kind", ""),
                                        data.get("payload") or {})
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Rooms: lost the event bus; trying again.", exc_info=True)
                await asyncio.sleep(5)

    def latest(self) -> int:
        conn = db.connect_for(self.db_path, "rooms", "chat_events")   # rooms.db once moved (Phase 3)
        try:
            return conn.execute("SELECT coalesce(max(id), 0) FROM chat_events").fetchone()[0]
        finally:
            conn.close()

    # ------------------------------------------------------------ streaming
    def _fetch(self, user_id: int, after: int, directory, seen: dict[int, tuple[float, bool]]) -> list[tuple]:
        """Events after `after` in rooms this person can see: [(id, kind, payload)], plus the last id looked at."""
        conn = db.connect_for(self.db_path, "rooms", "chat_events")   # rooms.db once moved (Phase 3)
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
        await asyncio.to_thread(self._still_here, user["id"], user["username"])
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
                    await asyncio.to_thread(self._still_here, user["id"], user["username"])
                    yield b": ping\n\n"
        finally:
            self.listeners.discard(lst)
            if not any(x.user_id == user["id"] for x in self.listeners):
                try:
                    events.forget(f"rooms:online:{user['id']}")   # another process's stream puts it back
                except Exception:
                    pass
                self._presence_changed()
