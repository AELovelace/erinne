"""D8 health: the worker's heartbeat, the queue, the models, and /healthz/full (mnemerynne/health.py)."""

from __future__ import annotations

import time

import httpx
import pytest

from mnemerynne import health, jobs, worker


@pytest.fixture(autouse=True)
def _fresh_probes(monkeypatch):
    monkeypatch.setattr(health, "_probe_cache", {})     # never reuse another test's probe


def _models(states: dict[str, int | None]) -> httpx.Client:
    """llama-servers by port: an HTTP status for GET /health, or None for one that doesn't answer."""
    def handler(request):
        code = states[request.url.port]
        if code is None:
            raise httpx.ConnectError("refused")
        assert request.url.path == "/health"
        return httpx.Response(code, json={"status": "ok" if code == 200 else "loading model"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_worker_counts_as_alive_while_it_beats_or_works(conn, settings):
    """Never seen, alive while its beat is fresh, busy on a recent job when quiet, else down."""
    now = time.time()
    assert health.worker(conn, settings, now) == {"state": "never", "beat_age": None}
    health.beat(conn, now - 30)
    assert health.worker(conn, settings, now) == {"state": "ok", "beat_age": 30}
    health.beat(conn, now - health.WORKER_STALE_SECONDS - 60)
    assert health.worker(conn, settings, now)["state"] == "down"
    job = jobs.enqueue(conn, "ingest")
    jobs.claim(conn)                                         # a long job blocks the loop: busy, not down
    assert health.worker(conn, settings, now)["state"] == "busy"
    conn.execute("UPDATE jobs SET locked_at = datetime('now', '-2 hours') WHERE id = ?", (job,))
    assert health.worker(conn, settings, now)["state"] == "down"   # past job_stale_minutes


def test_the_worker_beats_as_it_runs(conn, settings, monkeypatch):
    """`mnemerynne worker` records its heartbeat as soon as it is ready."""
    monkeypatch.setattr(worker, "Embedder", lambda s: type("E", (), {"close": lambda self: None})())
    worker.run(settings, once=True, poll_seconds=0)
    assert health.worker(conn, settings)["state"] == "ok"


def test_the_queue_is_counted_and_a_long_wait_is_stuck(conn):
    assert health.queue(conn) == {"queued": 0, "due": 0, "running": 0, "failed": 0, "oldest_wait": 0, "state": "ok"}
    for _ in range(3):
        jobs.enqueue(conn, "ingest")
    later = jobs.enqueue(conn, "ingest")
    conn.execute("UPDATE jobs SET run_after = datetime('now', '+1 hour') WHERE id = ?", (later,))   # a retry, not due
    conn.execute("UPDATE jobs SET status = 'failed' WHERE id = 1")
    got = health.queue(conn)
    assert (got["queued"], got["due"], got["failed"], got["state"]) == (3, 2, 1, "ok")
    conn.execute("UPDATE jobs SET run_after = datetime('now', '-2 hours') WHERE id = 2")
    got = health.queue(conn)
    assert got["state"] == "stuck" and got["oldest_wait"] >= 7200


def test_models_are_probed_on_their_health_endpoint(settings):
    """200 ok, 503 loading, anything else error, no answer down; the reranker only when set."""
    settings.llm_url, settings.embed_url = "http://127.0.0.1:8086/", "http://127.0.0.1:8085"
    client = _models({8086: 200, 8085: 503})
    assert health.models(settings, client) == {"generator": "ok", "embedder": "loading"}
    health._probe_cache.clear()
    settings.rerank_url = "http://127.0.0.1:8087"
    client = _models({8086: 500, 8085: None, 8087: 200})
    assert health.models(settings, client) == {"generator": "error", "embedder": "down", "reranker": "ok"}


def test_probes_are_reused_for_a_few_seconds():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert health.probe("http://m:1", client, now=100.0) == "ok"
    assert health.probe("http://m:1", client, now=105.0) == "ok" and len(calls) == 1
    assert health.probe("http://m:1", client, now=100.0 + health.PROBE_CACHE_SECONDS) == "ok" and len(calls) == 2


def test_healthz_full_answers_503_while_anything_fails(client, conn, monkeypatch):
    """/healthz/full lists every check (states and counts only) and is 503 until all pass;
    /healthz itself stays a plain 200 for update.sh and the TV boxes."""
    monkeypatch.setattr(health, "models", lambda settings, client=None: {"generator": "ok", "embedder": "loading"})
    got = client.get("/healthz/full")
    body = got.json()
    assert got.status_code == 503 and got.headers["cache-control"] == "no-store"
    assert body["status"] == "degraded" and body["failing"] == ["worker", "embedder"]
    assert body["worker"]["state"] == "never" and body["queue"]["state"] == "ok" and "schema" in body
    assert "127.0.0.1" not in got.text                       # no addresses on a page without sign-in

    health.beat(conn)
    monkeypatch.setattr(health, "models", lambda settings, client=None: {"generator": "ok", "embedder": "ok"})
    got = client.get("/healthz/full")
    assert got.status_code == 200 and got.json()["status"] == "ok" and got.json()["failing"] == []
    assert client.get("/healthz").status_code == 200
