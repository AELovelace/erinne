"""mnemerynne/events.py: live events between the services (docs/plans/services-rewrite.md).

The same behaviour from both backends: the in-process one (the all-in-one web app, tests) is tested always, and
the Valkey one against fakeredis always, and against a real Valkey (or Redis) too when
MNEMERYNNE_TEST_VALKEY_URL points at one."""

import asyncio
import os
import threading
import time
import uuid

import pytest

from mnemerynne import events

VALKEY = os.environ.get("MNEMERYNNE_TEST_VALKEY_URL", "")


def _buses():
    yield "local", events.LocalBus()
    import fakeredis                                          # a stand-in Valkey: the Valkey backend's own code runs
    server = fakeredis.FakeServer()
    yield "fake-valkey", events.ValkeyBus("fake", clients=(
        fakeredis.FakeRedis(server=server, decode_responses=True),
        fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)))
    if VALKEY:
        bus = events.ValkeyBus(VALKEY, prefix=f"mnemerynne-test-{uuid.uuid4().hex[:8]}:")   # nothing shared
        yield "valkey", bus


@pytest.fixture(params=["local", "fake-valkey", "valkey"])
def bus(request):
    found = dict(_buses())
    if request.param not in found:
        pytest.skip("set MNEMERYNNE_TEST_VALKEY_URL to test against a Valkey")
    yield found[request.param]
    asyncio.run(found[request.param].aclose())


async def _gather(bus, channels, count, publish):
    """Subscribe, let `publish()` send, and return the first `count` (channel, data) received."""
    got = []

    async def listen():
        async for item in bus.subscribe(*channels):
            got.append(item)
            if len(got) == count:
                return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.2)                                  # subscribed before anything is sent
    await publish()
    await asyncio.wait_for(task, 5)
    return got


def test_a_subscriber_hears_its_channels_and_patterns_only(bus):
    async def run():
        async def send():
            await bus.publish("rooms:12", {"id": 1})
            await bus.publish("rooms:13", {"id": 2})          # matches the pattern
            await bus.publish("screen:4", {"skip": True})     # not subscribed
            await bus.publish("user:7", {"badge": 3})
        return await _gather(bus, ("rooms:*", "user:7"), 3, send)

    got = asyncio.run(run())
    assert got == [("rooms:12", {"id": 1}), ("rooms:13", {"id": 2}), ("user:7", {"badge": 3})]


def test_a_worker_thread_can_publish_to_the_event_loops_subscribers(bus):
    """Most of the app runs in request worker threads: they publish with publish_sync."""
    async def run():
        async def send():
            sender = threading.Thread(target=bus.publish_sync, args=("rooms:12", {"from": "a thread"}))
            sender.start()
            sender.join()
        return await _gather(bus, ("rooms:12",), 1, send)

    assert asyncio.run(run()) == [("rooms:12", {"from": "a thread"})]


def test_expiring_keys_vanish_by_themselves(bus):
    bus.expiring_set("presence:party:AB12:user:7", "1", 0.3)
    bus.expiring_set("presence:party:AB12:user:8", "1", 30)
    assert bus.expiring_get("presence:party:AB12:user:7") == "1"
    assert bus.expiring_keys("presence:party:AB12:*") == ["presence:party:AB12:user:7", "presence:party:AB12:user:8"]
    time.sleep(0.5)
    assert bus.expiring_get("presence:party:AB12:user:7") is None
    assert bus.expiring_keys("presence:party:AB12:*") == ["presence:party:AB12:user:8"]


def test_a_counter_counts_within_its_window_then_starts_again(bus):
    assert [bus.count("login:jsmith", 0.4) for _ in range(3)] == [1, 2, 3]
    time.sleep(0.6)
    assert bus.count("login:jsmith", 0.4) == 1               # a new window
    bus.forget("login:jsmith")
    assert bus.count("login:jsmith", 0.4) == 1


def test_without_a_url_events_stay_in_the_process():
    assert isinstance(events.configure(""), events.LocalBus)


def test_a_valkey_that_doesnt_answer_doesnt_stop_the_app(caplog):
    with caplog.at_level("WARNING", logger="mnemerynne.events"):
        bus = events.configure("redis://127.0.0.1:1/0")       # nothing listens on port 1
    assert isinstance(bus, events.LocalBus)                   # carries on, in-process
    assert "isn't answering" in caplog.text
    events.configure("")
