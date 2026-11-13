"""security.GamesShed: game API requests beyond a few at once get an instant JSON 503 instead of queuing.

On the box, game polls came in faster than they finished and queued without bound (28,000 waiting at once, each
holding its request in memory), until the web app ran out of memory and every page failed with it."""

import asyncio
import json

from mnemerynne.web.security import GamesShed


def _run(shed, path):
    """One request through the middleware; returns (status, body) once it's answered."""
    got = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            got["status"] = message["status"]
        elif message["type"] == "http.response.body":
            got["body"] = got.get("body", b"") + message.get("body", b"")

    async def go():
        await shed({"type": "http", "path": path, "method": "GET", "headers": []}, receive, send)
        return got.get("status"), got.get("body", b"")
    return go()


def test_requests_past_the_limit_are_answered_at_once_and_streams_and_other_pages_never_count():
    async def scenario():
        release = asyncio.Event()                      # holds the slow requests until we let them go

        async def slow_app(scope, receive, send):      # a game request stuck waiting (as on the box)
            await release.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        shed = GamesShed(slow_app)
        held = [asyncio.create_task(_run(shed, "/api/media/games/lumi-pet/state")) for _ in range(GamesShed.LIMIT)]
        stream = asyncio.create_task(_run(shed, "/api/media/games/lumi-pet/parties/AB12/stream"))
        files = asyncio.create_task(_run(shed, "/api/browse"))
        await asyncio.sleep(0.01)
        assert shed.busy == GamesShed.LIMIT            # every slot taken; the stream and Files aren't counted

        status, body = await asyncio.wait_for(_run(shed, "/api/media/games/lumi-pet/state"), 1)
        assert status == 503                           # answered at once, not queued
        assert "busy" in json.loads(body)["detail"]    # JSON: old game pages read it as "try again next time"

        release.set()                                  # the held requests finish...
        results = await asyncio.gather(*held, stream, files)
        assert all(status == 200 for status, _ in results)
        assert shed.busy == 0                          # ...and give their slots back

        status, _ = await _run(shed, "/api/media/games/lumi-pet/state")   # (a quick app now: the event is set)
        assert status == 200                           # room again

    asyncio.run(scenario())


def test_a_cancelled_request_gives_its_slot_back():
    async def scenario():
        async def never(scope, receive, send):
            await asyncio.sleep(60)

        shed = GamesShed(never)
        task = asyncio.create_task(_run(shed, "/api/media/games/lumi-pet/state"))
        await asyncio.sleep(0.01)
        assert shed.busy == 1
        task.cancel()                                  # the client went away
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert shed.busy == 0

    asyncio.run(scenario())


def test_a_slow_request_is_logged_by_name_and_a_quick_one_isnt(caplog, monkeypatch):
    """SlowRequestLog: what took over a second to start answering shows up in the journal by name."""
    from mnemerynne.web.security import SlowRequestLog

    async def app(scope, receive, send):
        await asyncio.sleep(0.05 if scope["path"] == "/slow" else 0)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await asyncio.sleep(0.2)                       # the body taking long isn't slowness: it's already answering
        await send({"type": "http.response.body", "body": b"{}"})

    monkeypatch.setattr(SlowRequestLog, "SLOW_SECONDS", 0.03)
    log = SlowRequestLog(app)
    with caplog.at_level("WARNING", logger="mnemerynne.web.slow"):
        assert asyncio.run(_run(log, "/slow"))[0] == 200
        assert asyncio.run(_run(log, "/quick"))[0] == 200
    lines = [r.getMessage() for r in caplog.records if r.name == "mnemerynne.web.slow"]
    assert len(lines) == 1 and lines[0].startswith("Slow request: GET /slow -> 200 in ")
