"""Health beyond /healthz (D8): is the worker alive, how deep is its queue, are the models up.

/healthz stays what it was, a liveness answer from the web process and the database: update.sh,
setup.sh and the TV boxes treat any non-200 as "the server is down", and a model that is still
loading isn't that. /healthz/full (web/app.py) adds the checks here and answers 503 when one fails,
for a monitor to watch. It shows states, counts and ages only: no addresses, file names or errors,
since it needs no sign-in.

- worker: the worker process writes a heartbeat into meta (beat()) every HEARTBEAT_SECONDS. It
  counts as alive while that is fresh, or while a job it claimed is still within job_stale_minutes
  (one long OCR job blocks the loop, so it can't beat meanwhile).
- queue: jobs by status, how many are due now, and how long the oldest due one has waited.
- models: llama-server's GET /health for the generator (llm_url), the embedder (embed_url) and the
  reranker if one is set (rerank_url): 200 = ok, 503 = loading, no answer = down.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any

import httpx

from mnemerynne.config import Settings

HEARTBEAT_KEY = "worker_heartbeat"     # meta key: unix time of the worker's last beat
HEARTBEAT_SECONDS = 15                 # how often the worker beats (worker.run)
WORKER_STALE_SECONDS = 600             # no beat for this long, and no job running: the worker is down
PROBE_TIMEOUT = 2.0                    # seconds per model probe
PROBE_CACHE_SECONDS = 10               # a monitor polling hard reuses the last probe
QUEUE_WAIT_WARN_SECONDS = 1800         # a due job waiting longer than this: the queue is stuck

_probe_lock = threading.Lock()
_probe_cache: dict[str, tuple[float, str]] = {}   # url -> (when probed, state)


# ------------------------------------------------------------------ worker
def beat(conn: sqlite3.Connection, now: float | None = None) -> None:
    """The worker is alive: record when (worker.run calls this every HEARTBEAT_SECONDS)."""
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                 (HEARTBEAT_KEY, str(int(time.time() if now is None else now))))   # whole seconds


def worker(conn: sqlite3.Connection, settings: Settings, now: float | None = None) -> dict[str, Any]:
    """{state: ok | busy | down | never, beat_age: seconds since the last beat or None}."""
    now = time.time() if now is None else now
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (HEARTBEAT_KEY,)).fetchone()
    age = max(0, int(now) - int(float(row["value"]))) if row and row["value"] else None   # whole seconds too
    if age is not None and age <= WORKER_STALE_SECONDS:
        return {"state": "ok", "beat_age": age}
    # Quiet, but working on a job it claimed recently enough (a long OCR job blocks the loop).
    running = conn.execute(
        "SELECT count(*) FROM jobs WHERE status = 'running' AND locked_at >= datetime('now', ?)",
        (f"-{int(settings.job_stale_minutes)} minutes",)).fetchone()[0]
    if running:
        return {"state": "busy", "beat_age": age}
    return {"state": "never" if age is None else "down", "beat_age": age}


# ------------------------------------------------------------------- queue
def queue(conn: sqlite3.Connection) -> dict[str, Any]:
    """Jobs by status, how many are due now, and the oldest due job's wait in seconds."""
    counts = {r[0]: r[1] for r in conn.execute("SELECT status, count(*) FROM jobs GROUP BY status")}
    due, oldest = conn.execute(
        "SELECT count(*), CAST(strftime('%s', 'now') - strftime('%s', min(run_after)) AS INTEGER) "
        "FROM jobs WHERE status = 'queued' AND run_after <= datetime('now')").fetchone()
    return {"queued": counts.get("queued", 0), "due": due, "running": counts.get("running", 0),
            "failed": counts.get("failed", 0), "oldest_wait": max(0, oldest) if due else 0,
            "state": "stuck" if due and oldest > QUEUE_WAIT_WARN_SECONDS else "ok"}


# ------------------------------------------------------------------ models
def probe(url: str, client: httpx.Client | None = None, now: float | None = None) -> str:
    """One llama-server's state: ok, loading (503 while the model loads), error or down."""
    now = time.monotonic() if now is None else now
    with _probe_lock:
        cached = _probe_cache.get(url)
        if cached and now - cached[0] < PROBE_CACHE_SECONDS:
            return cached[1]
    own = client is None
    client = client or httpx.Client(timeout=PROBE_TIMEOUT)
    try:
        got = client.get(url.rstrip("/") + "/health")
        state = "ok" if got.status_code == 200 else "loading" if got.status_code == 503 else "error"
    except httpx.HTTPError:
        state = "down"
    finally:
        if own:
            client.close()
    with _probe_lock:
        _probe_cache[url] = (now, state)
    return state


def models(settings: Settings, client: httpx.Client | None = None) -> dict[str, str]:
    """The generator, the embedder and (if set) the reranker, each by probe()."""
    urls = {"generator": settings.llm_url, "embedder": settings.embed_url}
    if settings.rerank_url:
        urls["reranker"] = settings.rerank_url
    return {name: probe(url, client) for name, url in urls.items()}


# ------------------------------------------------------------------- all
def report(conn: sqlite3.Connection, settings: Settings, client: httpx.Client | None = None) -> dict[str, Any]:
    """Every check, and the overall status: ok, or degraded with the names of what failed."""
    checks = {"worker": worker(conn, settings), "queue": queue(conn), "models": models(settings, client)}
    failing = [] if checks["worker"]["state"] in ("ok", "busy") else ["worker"]
    failing += [] if checks["queue"]["state"] == "ok" else ["queue"]
    failing += [name for name, state in checks["models"].items() if state != "ok"]
    return {"status": "degraded" if failing else "ok", "failing": failing, **checks}
