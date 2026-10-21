"""Ingestion worker: `mnemerynne worker`. Runs as its own process/systemd unit."""

from __future__ import annotations

import logging
import signal
import sqlite3
import time

from mnemerynne import db, fs, jobs
from mnemerynne.clients.embed import Embedder
from mnemerynne.config import Settings
from mnemerynne.ingest import pipeline

log = logging.getLogger("mnemerynne.worker")


def _mark_file(conn: sqlite3.Connection, file_id: int | None, **fields) -> None:
    if file_id is None:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE files SET {sets}, updated_at = datetime('now') WHERE id = ?", (*fields.values(), file_id))


def run_one(conn: sqlite3.Connection, settings: Settings, embedder: Embedder) -> bool:
    """Process one due job. Returns False when the queue had nothing due."""
    job = jobs.claim(conn)
    if job is None:
        return False
    try:
        if job["kind"] != "ingest":
            raise pipeline.IngestError(f"Unknown job kind '{job['kind']}'.")
        pipeline.process_file(conn, settings, embedder, job["file_id"])
    except pipeline.IngestError as exc:
        # The file itself is the problem; retrying won't help.
        log.warning("Job %s (file %s) failed: %s", job["id"], job["file_id"], exc)
        jobs.fail(conn, job, str(exc), max_attempts=0)
        _mark_file(conn, job["file_id"], status="failed", progress=None, error=str(exc))
    except Exception as exc:  # embedder down, disk full, ...: retry with backoff
        message = f"{type(exc).__name__}: {exc}"
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        retrying = jobs.fail(conn, job, message, settings.job_max_attempts)
        log.warning("Job %s (file %s) error%s: %s", job["id"], job["file_id"],
                    ", will retry" if retrying else ", giving up", message)
        if retrying:
            _mark_file(conn, job["file_id"], status="queued", progress=f"Retrying soon ({message})")
        else:
            _mark_file(conn, job["file_id"], status="failed", progress=None, error=message)
    else:
        jobs.complete(conn, job["id"])
    return True


def run(settings: Settings, *, once: bool = False, poll_seconds: float = 2.0) -> None:
    conn = db.connect(settings.db_path)
    db.migrate(conn, settings.embed_dim)
    embedder = Embedder(settings)
    stopping = False

    def _stop(signum, frame):
        nonlocal stopping
        stopping = True
        log.info("Stopping after the current job.")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    recovered = jobs.recover_stale(conn, settings.job_stale_minutes)
    if recovered:
        log.info("Requeued %d job(s) left running by a previous worker.", recovered)
    log.info("Worker ready (embedder %s).", settings.embed_url)
    last_recover = time.monotonic()
    last_purge = 0.0
    try:
        while not stopping:
            did_work = run_one(conn, settings, embedder)
            if once and not did_work:
                break
            if not did_work:
                time.sleep(poll_seconds)
            if time.monotonic() - last_recover > 300:
                jobs.recover_stale(conn, settings.job_stale_minutes)
                last_recover = time.monotonic()
            if time.monotonic() - last_purge > 3600:
                last_purge = time.monotonic()
                try:
                    removed = fs.purge_expired(conn, settings, settings.trash_days)
                    if removed:
                        log.info("Emptied %d recycle-bin item(s) older than %d days.", removed, settings.trash_days)
                except Exception:  # never let housekeeping stop ingestion
                    log.exception("Recycle bin cleanup failed")
    finally:
        embedder.close()
        conn.close()
