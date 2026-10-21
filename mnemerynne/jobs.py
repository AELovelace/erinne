"""A small job queue in SQLite, claimed atomically by the worker process."""

from __future__ import annotations

import sqlite3


def enqueue(conn: sqlite3.Connection, kind: str, file_id: int | None = None) -> int:
    return conn.execute("INSERT INTO jobs(kind, file_id) VALUES (?, ?)", (kind, file_id)).lastrowid


def claim(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Atomically take the oldest due job, marking it running."""
    return conn.execute(
        "UPDATE jobs SET status = 'running', locked_at = datetime('now'), attempts = attempts + 1 "
        "WHERE id = (SELECT id FROM jobs WHERE status = 'queued' AND run_after <= datetime('now') "
        "            ORDER BY id LIMIT 1) "
        "RETURNING *"
    ).fetchone()


def complete(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("UPDATE jobs SET status = 'done', locked_at = NULL, error = NULL WHERE id = ?", (job_id,))


def fail(conn: sqlite3.Connection, job: sqlite3.Row, error: str, max_attempts: int) -> bool:
    """Record a failure. Returns True if the job will be retried."""
    if job["attempts"] < max_attempts:
        delay = 30 * 2 ** (job["attempts"] - 1)  # 30 s, 60 s, 120 s, ...
        conn.execute(
            "UPDATE jobs SET status = 'queued', locked_at = NULL, error = ?, "
            "run_after = datetime('now', ?) WHERE id = ?",
            (error, f"+{delay} seconds", job["id"]),
        )
        return True
    conn.execute(
        "UPDATE jobs SET status = 'failed', locked_at = NULL, error = ? WHERE id = ?", (error, job["id"])
    )
    return False


def recover_stale(conn: sqlite3.Connection, minutes: int) -> int:
    """Requeue jobs left 'running' by a worker that died."""
    return conn.execute(
        "UPDATE jobs SET status = 'queued', locked_at = NULL "
        "WHERE status = 'running' AND locked_at < datetime('now', ?)",
        (f"-{int(minutes)} minutes",),
    ).rowcount
