"""Live events between Mnemerynne's processes (docs/plans/services-rewrite.md, "Live events").

Each feature runs as a process of its own; what used to be an in-memory queue in the one web process (a new
Rooms message, a TV command, a share permission change, a theme change) becomes a message on a channel here.
The database stays the record: an event only says "look now".

    events.configure(settings.valkey_url)          # at start: Valkey, or in-process when it's empty
    events.publish_sync("rooms:12", {"id": 99})    # from a request's worker thread (most of the app)
    await events.publish("screen:4", {...})        # from async code
    async for channel, data in events.subscribe("rooms:12", "user:7"):   # or a pattern: "rooms:*"
        ...
    events.expiring_set("presence:party:AB12:user:7", "1", 30)   # a key that vanishes by itself (presence)
    events.count("login:jsmith", 300)              # a counter within a window (throttling, rate limits)

Two backends with the same behaviour (messages are JSON either way):
- LocalBus: one process (the all-in-one web app, tests, development). Events never leave the process.
- ValkeyBus: MNEMERYNNE_VALKEY_URL set (redis://... or unix:///run/valkey/valkey.sock): every process on
  the box hears every other. Valkey speaks the Redis protocol; the redis client talks to it.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import threading
import time
from typing import AsyncIterator

log = logging.getLogger("mnemerynne.events")


class LocalBus:
    """Events inside this one process. Subscribers are asyncio queues on their own event loops; publishing works
    from any thread (a request's worker thread publishes, the event loop's streams receive)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list[tuple[tuple[str, ...], asyncio.AbstractEventLoop, asyncio.Queue]] = []   # patterns, loop, queue
        self._keys: dict[str, tuple[str, float]] = {}          # key -> (value, expires at, time.monotonic)
        self._counts: dict[str, tuple[int, float]] = {}        # key -> (count, window ends at)

    def publish_sync(self, channel: str, data: dict) -> int:
        """Hand the event to every subscriber whose channels match; returns how many got it."""
        text = json.dumps(data)                                 # the same round trip as through Valkey
        with self._lock:
            targets = [(loop, queue) for patterns, loop, queue in self._subs
                       if any(fnmatch.fnmatchcase(channel, p) for p in patterns)]
        for loop, queue in targets:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, (channel, json.loads(text)))
            except RuntimeError:                                # that subscriber's loop has closed
                pass
        return len(targets)

    async def publish(self, channel: str, data: dict) -> int:
        return self.publish_sync(channel, data)

    async def subscribe(self, *channels: str) -> AsyncIterator[tuple[str, dict]]:
        """(channel, data) as they come, for channels or patterns ("rooms:*"), until the caller stops."""
        queue: asyncio.Queue = asyncio.Queue()
        entry = (tuple(channels), asyncio.get_running_loop(), queue)
        with self._lock:
            self._subs.append(entry)
        try:
            while True:
                yield await queue.get()
        finally:
            with self._lock:
                self._subs.remove(entry)

    def expiring_set(self, key: str, value: str, seconds: float) -> None:
        with self._lock:
            self._keys[key] = (value, time.monotonic() + seconds)

    def expiring_get(self, key: str) -> str | None:
        with self._lock:
            found = self._keys.get(key)
            if found is None or found[1] <= time.monotonic():
                self._keys.pop(key, None)
                return None
            return found[0]

    def expiring_keys(self, pattern: str) -> list[str]:
        """Keys still alive that match the pattern (presence: who's at this table)."""
        now = time.monotonic()
        with self._lock:
            for key in [k for k, (_, at) in self._keys.items() if at <= now]:
                del self._keys[key]
            return sorted(k for k in self._keys if fnmatch.fnmatchcase(k, pattern))

    def count(self, key: str, window: float) -> int:
        """Add one to the counter and return it; the counter starts again `window` seconds after its first one."""
        now = time.monotonic()
        with self._lock:
            n, ends = self._counts.get(key, (0, 0.0))
            if ends <= now:
                n, ends = 0, now + window
            self._counts[key] = (n + 1, ends)
            return n + 1

    def forget(self, key: str) -> None:
        with self._lock:
            self._keys.pop(key, None)
            self._counts.pop(key, None)

    async def aclose(self) -> None:
        pass


class ValkeyBus:
    """Events between processes through Valkey. A plain client for the worker threads, an asyncio one for the
    event loop's subscribers. Keys get a prefix so a shared Valkey can't mix up two installs."""

    def __init__(self, url: str, prefix: str = "mnemerynne:", clients=None) -> None:
        """`clients`: (plain, asyncio) clients already made, both on the same server (tests use fakeredis's)."""
        self.url, self.prefix = url, prefix
        if clients is not None:
            self._sync, self._async = clients
            return
        import redis                                            # only needed with Valkey configured
        import redis.asyncio

        self._sync = redis.Redis.from_url(url, decode_responses=True)
        self._async = redis.asyncio.Redis.from_url(url, decode_responses=True)

    def _k(self, name: str) -> str:
        return self.prefix + name

    def publish_sync(self, channel: str, data: dict) -> int:
        return int(self._sync.publish(self._k(channel), json.dumps(data)))

    async def publish(self, channel: str, data: dict) -> int:
        return int(await self._async.publish(self._k(channel), json.dumps(data)))

    async def subscribe(self, *channels: str) -> AsyncIterator[tuple[str, dict]]:
        pubsub = self._async.pubsub(ignore_subscribe_messages=True)
        exact = [self._k(c) for c in channels if not any(ch in c for ch in "*?[")]
        patterns = [self._k(c) for c in channels if any(ch in c for ch in "*?[")]
        if exact:
            await pubsub.subscribe(*exact)
        if patterns:
            await pubsub.psubscribe(*patterns)
        try:
            async for message in pubsub.listen():
                if message.get("type") not in ("message", "pmessage"):
                    continue
                try:
                    yield message["channel"][len(self.prefix):], json.loads(message["data"])
                except (ValueError, TypeError):                 # not ours (someone else on the channel)
                    continue
        finally:
            await pubsub.aclose()

    def expiring_set(self, key: str, value: str, seconds: float) -> None:
        self._sync.set(self._k(key), value, px=max(1, int(seconds * 1000)))

    def expiring_get(self, key: str) -> str | None:
        return self._sync.get(self._k(key))

    def expiring_keys(self, pattern: str) -> list[str]:
        return sorted(k[len(self.prefix):] for k in self._sync.scan_iter(self._k(pattern), count=500))

    def count(self, key: str, window: float) -> int:
        with self._sync.pipeline() as pipe:                     # one round trip: add, and start the window if new
            pipe.incr(self._k(key))
            pipe.pexpire(self._k(key), max(1, int(window * 1000)), nx=True)
            n, _ = pipe.execute()
        return int(n)

    def forget(self, key: str) -> None:
        self._sync.delete(self._k(key))

    async def aclose(self) -> None:
        await self._async.aclose()
        self._sync.close()


_bus: LocalBus | ValkeyBus = LocalBus()      # until configure(): events stay in this process


def configure(url: str | None) -> LocalBus | ValkeyBus:
    """At start (the web app and every service): Valkey when a URL is set, else in-process. A Valkey that
    doesn't answer is logged and the process carries on in-process rather than not starting."""
    global _bus
    if not url:
        _bus = LocalBus()
        return _bus
    try:
        bus = ValkeyBus(url)
        bus._sync.ping()
    except Exception as exc:                                    # unreachable, or the client is missing
        log.warning("Events: Valkey at %s isn't answering (%s); events stay in this process.", url, exc)
        _bus = LocalBus()
        return _bus
    log.info("Events: through Valkey at %s.", url)
    _bus = bus
    return _bus


def current() -> LocalBus | ValkeyBus:
    return _bus


def publish_sync(channel: str, data: dict) -> int:
    return _bus.publish_sync(channel, data)


async def publish(channel: str, data: dict) -> int:
    return await _bus.publish(channel, data)


def subscribe(*channels: str) -> AsyncIterator[tuple[str, dict]]:
    return _bus.subscribe(*channels)


def expiring_set(key: str, value: str, seconds: float) -> None:
    _bus.expiring_set(key, value, seconds)


def expiring_get(key: str) -> str | None:
    return _bus.expiring_get(key)


def expiring_keys(pattern: str) -> list[str]:
    return _bus.expiring_keys(pattern)


def count(key: str, window: float) -> int:
    return _bus.count(key, window)


def forget(key: str) -> None:
    _bus.forget(key)
