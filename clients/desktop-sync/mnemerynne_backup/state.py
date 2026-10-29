"""What the app last backed up (state.db): per file its size, modified time, hash and the server's version.

Only the thread running a pass writes to it. Losing it costs nothing but time: the next pass compares hashes with
the server's list and uploads only what differs.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    root_id  INTEGER NOT NULL,
    path     TEXT NOT NULL,
    size     INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256   TEXT NOT NULL,
    version  TEXT NOT NULL,       -- the server's version of it (its sha256 there)
    PRIMARY KEY (root_id, path)
);
CREATE TABLE IF NOT EXISTS uploads (  -- big files on their way up in parts
    root_id   INTEGER NOT NULL,
    path      TEXT NOT NULL,
    upload_id TEXT NOT NULL,
    sha256    TEXT NOT NULL,
    PRIMARY KEY (root_id, path)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Known:
    size: int
    mtime_ns: int
    sha256: str
    version: str


def _locked(fn):
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    return wrapper


class State:
    """Shared by a pass's upload threads (resumable uploads), so every call holds the lock."""

    def __init__(self, path: Path | str):
        self.lock = threading.RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode = WAL") if str(path) != ":memory:" else None
        self.conn.executescript(SCHEMA)

    @_locked
    def close(self) -> None:
        self.conn.close()

    @_locked
    def files(self, root_id: int) -> dict[str, Known]:
        return {r[0]: Known(*r[1:]) for r in self.conn.execute(
            "SELECT path, size, mtime_ns, sha256, version FROM files WHERE root_id = ?", (root_id,))}

    @_locked
    def put(self, root_id: int, path: str, k: Known) -> None:
        self.conn.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?)",
                          (root_id, path, k.size, k.mtime_ns, k.sha256, k.version))

    @_locked
    def drop(self, root_id: int, path: str) -> None:
        """A file, or a folder and everything under it."""
        self.conn.execute("DELETE FROM files WHERE root_id = ? AND (path = ? OR path LIKE ? ESCAPE '\\')",
                          (root_id, path, _prefix(path)))

    @_locked
    def rename(self, root_id: int, old: str, new: str) -> None:
        """A file or a folder moved on the server: its rows follow."""
        rows = self.conn.execute("SELECT path FROM files WHERE root_id = ? AND (path = ? OR path LIKE ? ESCAPE '\\')",
                                 (root_id, old, _prefix(old))).fetchall()
        with self.conn:
            for (path,) in rows:
                self.conn.execute("UPDATE files SET path = ? WHERE root_id = ? AND path = ?",
                                  (new + path[len(old):], root_id, path))

    @_locked
    def forget_root(self, root_id: int) -> None:
        self.conn.execute("DELETE FROM files WHERE root_id = ?", (root_id,))
        self.conn.execute("DELETE FROM uploads WHERE root_id = ?", (root_id,))

    # resumable uploads
    @_locked
    def upload(self, root_id: int, path: str, sha256: str) -> str | None:
        row = self.conn.execute("SELECT upload_id FROM uploads WHERE root_id = ? AND path = ? AND sha256 = ?",
                                (root_id, path, sha256)).fetchone()
        return row[0] if row else None

    @_locked
    def set_upload(self, root_id: int, path: str, sha256: str, upload_id: str | None) -> None:
        if upload_id is None:
            self.conn.execute("DELETE FROM uploads WHERE root_id = ? AND path = ?", (root_id, path))
        else:
            self.conn.execute("INSERT OR REPLACE INTO uploads VALUES (?, ?, ?, ?)", (root_id, path, upload_id, sha256))

    # small bits of status
    @_locked
    def get(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @_locked
    def set(self, key: str, value) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))


def _prefix(path: str) -> str:
    return path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
