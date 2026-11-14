"""Search's index (chunks, their keyword index and vectors) kept in step with the files it describes.

Since Phase 6a of the services rewrite (docs/plans/services-rewrite.md) the index lives in search.db, its own file
with its own write lock, while the files live in core. Each chunk carries a copy of where its file is (collection,
folder, or the bin) and whether Claude may see it, because search filters on those inside its queries. A change to
a file therefore changes two files, and SQLite can't commit two files as one: core commits first, then this module
copies the file's new state onto its chunks (after_commit), reading it back from core. Until that copy lands,
retrieval leaves out any chunk whose copy disagrees with its file (retrieval._load_hits, current=True), so a moved
or hidden file is never found where it no longer is; repair() fixes whatever a crash in between left behind.

Before the move (a database only core's migrations ran on, many tests) chunks are still in core: the same calls
then change them in the caller's own transaction, as before.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import Iterable, Iterator

from mnemerynne import db

log = logging.getLogger("mnemerynne.search_index")


def local(conn: sqlite3.Connection) -> bool:
    """Whether `conn`'s own database holds the chunks (core before the move, or search.db's connection)."""
    return conn.execute("SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = 'chunks'").fetchone() \
        is not None


def _core_path(conn: sqlite3.Connection) -> str:
    """The core database's file, from a core connection or a feature's (core attached as `core`)."""
    files = {row["name"]: row["file"] for row in conn.execute("PRAGMA database_list")}
    return files.get("core") or files["main"]


@contextmanager
def on(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A connection that can write the chunks: `conn` itself before the move, else one to search.db (core
    attached read-only, so the files' rows read by their plain names), opened for the block."""
    if local(conn):
        yield conn
        return
    search = db.connect_feature(_core_path(conn), "search")
    try:
        yield search
    finally:
        search.close()


def expected(conn: sqlite3.Connection, file_id: int) -> tuple[int, int, int] | None:
    """What a file's chunks should carry, (collection_id, folder_id, claude_hidden), read from its row: the bin
    (fs.TRASH_COLLECTION) for a file in the bin or inside a folder in the bin. None: the file is gone."""
    from mnemerynne import fs

    row = conn.execute("SELECT claude_hidden FROM files WHERE id = ?", (file_id,)).fetchone()
    if row is None:
        return None
    collection_id, folder_id = fs.search_scope(conn, file_id)
    return collection_id, folder_id, int(row["claude_hidden"])


def _apply(search: sqlite3.Connection, file_id: int, want: tuple[int, int, int] | None) -> None:
    """Point one file's chunks and vectors at `want`, or delete them (None), on the connection holding them."""
    if want is None:
        search.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))      # vectors follow (trigger)
        return
    search.execute("UPDATE chunks SET collection_id = ?, folder_id = ?, claude_hidden = ? WHERE file_id = ?",
                   (*want, file_id))
    search.execute("UPDATE chunk_vec SET collection_id = ?, folder_id = ?, claude_hidden = ? "
                   "WHERE rowid IN (SELECT id FROM chunks WHERE file_id = ?)", (*want, file_id))


def sync(conn: sqlite3.Connection, file_ids: Iterable[int]) -> None:
    """These files moved, went to or came back from the bin, or were hidden from or shown to Claude: bring their
    chunks along. Call it inside the transaction that made the change (on a core connection). Before the move
    it's done right there, in that transaction; after it, once that transaction commits (resync)."""
    ids = sorted(set(file_ids))
    if not ids:
        return
    if local(conn):
        for file_id in ids:
            _apply(conn, file_id, expected(conn, file_id))
        return
    core_path = _core_path(conn)
    db.after_commit(conn, lambda: resync(core_path, ids))


def drop(conn: sqlite3.Connection, file_ids: Iterable[int]) -> None:
    """These files left search (deleted for good, or a backup made unsearchable): their chunks go. Inside the
    caller's transaction before the move; after its commit once moved."""
    ids = sorted(set(file_ids))
    if not ids:
        return
    if local(conn):
        for file_id in ids:
            _apply(conn, file_id, None)
        return
    core_path = _core_path(conn)
    db.after_commit(conn, lambda: _drop_now(core_path, ids))


def _drop_now(core_path: str, file_ids: list[int]) -> None:
    search = db.connect_feature(core_path, "search")
    try:
        with db.transaction(search):
            for file_id in file_ids:
                _apply(search, file_id, None)
    finally:
        search.close()


def resync(core_path: str, file_ids: Iterable[int]) -> int:
    """Copy each file's state from core (as committed) onto its chunks in search.db; a file that's gone loses
    them. Reads core inside search.db's write transaction, so an indexing swap (which reads core the same way)
    and this never cross: whichever runs second sees the other's result. Returns the files changed."""
    search = db.connect_feature(core_path, "search")
    changed = 0
    try:
        with db.transaction(search):
            for file_id in file_ids:
                have = search.execute("SELECT collection_id, folder_id, claude_hidden FROM chunks WHERE file_id = ? "
                                      "LIMIT 1", (file_id,)).fetchone()
                if have is None:
                    continue                                     # nothing indexed: nothing to carry
                want = expected(search, file_id)
                if want is None or tuple(have) != want:
                    _apply(search, file_id, want)
                    changed += 1
    finally:
        search.close()
    return changed


def repair(core_path: str) -> int:
    """Find every file whose chunks disagree with it (a crash between core's commit and the copy, an update that
    failed) and fix them; chunks of files that are gone go too. The web app's hourly tidy-up runs it, after the
    orphan sweep. Returns the files fixed. Nothing to do before the move."""
    if not db.feature_path(core_path, "search").exists():
        return 0
    from mnemerynne import fs

    search = db.connect_feature(core_path, "search")
    try:
        if not local(search):
            return 0
        rows = search.execute(
            "SELECT c.file_id, min(c.collection_id) AS cid, max(c.collection_id) AS cid2, min(c.folder_id) AS fid, "
            "max(c.folder_id) AS fid2, min(c.claude_hidden) AS hid, max(c.claude_hidden) AS hid2, "
            "f.id AS present, f.collection_id AS f_cid, f.folder_id AS f_fid, f.deleted_at, f.claude_hidden AS f_hid "
            "FROM chunks c LEFT JOIN core.files f ON f.id = c.file_id GROUP BY c.file_id").fetchall()
        live: dict[int, frozenset[int]] = {}
        wrong = []
        for r in rows:
            if r["present"] is None:
                wrong.append(r["file_id"])
                continue
            if r["f_cid"] not in live:
                live[r["f_cid"]] = fs.live_folder_ids(search, r["f_cid"])
            in_bin = r["deleted_at"] is not None or (r["f_fid"] is not None and r["f_fid"] not in live[r["f_cid"]])
            want = (fs.TRASH_COLLECTION if in_bin else r["f_cid"], r["f_fid"] or 0, int(r["f_hid"]))
            if (r["cid"], r["fid"], r["hid"]) != want or (r["cid2"], r["fid2"], r["hid2"]) != want:
                wrong.append(r["file_id"])
    finally:
        search.close()
    if not wrong:
        return 0
    fixed = resync(core_path, wrong)
    log.info("Search index repaired for %d file(s).", fixed)
    return fixed
