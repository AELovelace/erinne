"""Where Ask's tables live: assistant.db once the web app's start has moved them (docs/plans/services-rewrite.md,
Phase 7), core before that (a database only core's migrations ran on: the worker's first start, many tests).

Conversations, their messages, sources and ratings, the graph's timings, and LangGraph's checkpoints and store (the
memories) all live there. Any connection *reads* them by their plain names (assistant.db is attached read-only to
every request connection); a write goes through `on(conn)` or the request's `get_assistant_db`.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from mnemerynne import db

FEATURE = "assistant"


def local(conn: sqlite3.Connection) -> bool:
    """Whether `conn`'s own database holds the conversations (core before the move, or assistant.db's connection)."""
    return conn.execute("SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = 'conversations'") \
        .fetchone() is not None


def core_path(conn: sqlite3.Connection) -> str:
    """The core database's file, from a core connection or a feature's (core attached as `core`)."""
    files = {row[1]: row[2] for row in conn.execute("PRAGMA database_list")}
    return files.get("core") or files["main"]


@contextmanager
def on(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A connection that can write Ask's tables: `conn` itself while they're in its file, else one to
    assistant.db (core attached read-only, so users and files still read by their plain names), opened for the
    block. Its writes commit on their own: a core transaction around the block doesn't hold them back."""
    if local(conn):
        yield conn
        return
    own = db.connect_feature(core_path(conn), FEATURE)
    try:
        yield own
    finally:
        own.close()


def path(core: Path | str) -> Path:
    """The file LangGraph's checkpointer and store open: assistant.db once it holds their tables, else core (a
    database the web app hasn't started on yet)."""
    found = db.feature_path(core, FEATURE)
    if found.exists():
        conn = sqlite3.connect(found)
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'checkpoints'").fetchone():
                return found
        finally:
            conn.close()
    return Path(core)
