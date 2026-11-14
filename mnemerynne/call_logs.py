"""Where the call logs live: logs.db once the web app's start has moved them (docs/plans/services-rewrite.md,
Phase 8), core before that (a database only core's migrations ran on: the worker's first start, many tests).

mcp_calls (Claude's tool calls), api_calls (internal API calls) and agent_calls (Erynne's tool calls) are written on
every call, from the web app and every service, so their writes take logs.db's lock instead of core's. They read by
their plain names on any connection (logs.db is attached read-only to every request connection); a write goes
through `on(conn)`.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from mnemerynne import db

FEATURE = "logs"
_pools: dict[str, db.Pool] = {}          # one per core file, for this process: a call log write needs no new connection
_lock = threading.Lock()


def local(conn: sqlite3.Connection) -> bool:
    """Whether `conn`'s own database holds the call logs (core before the move, or logs.db's connection)."""
    return conn.execute("SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = 'mcp_calls'") \
        .fetchone() is not None


def _core_path(conn: sqlite3.Connection) -> str:
    files = {row[1]: row[2] for row in conn.execute("PRAGMA database_list")}
    return files.get("core") or files["main"]


def _pool(core: str) -> db.Pool:
    with _lock:
        if core not in _pools:
            _pools[core] = db.Pool(db.feature_path(core, FEATURE), keep=4,
                                   opener=lambda: db.connect_feature(core, FEATURE))
        return _pools[core]


@contextmanager
def on(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A connection that can write the call logs: `conn` itself while they're in its file, else one of this process's
    logs.db connections, lent for the block. Its writes commit on their own: a core transaction around the block
    doesn't hold them back, and its rollback doesn't take a call's log row with it (the call happened)."""
    if local(conn):
        yield conn
        return
    pool = _pool(_core_path(conn))
    own = pool.get()
    try:
        yield own
    finally:
        pool.put(own)


def close() -> None:
    """Close this process's logs.db connections (tests, shutdown)."""
    with _lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        pool.close()
