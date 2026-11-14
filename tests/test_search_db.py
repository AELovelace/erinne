"""search.db (docs/plans/services-rewrite.md, Phase 6a): search's index (chunks, their keyword index and vectors)
lives in a file of its own, so indexing bursts don't hold core's write lock. Each chunk's copy of where its file
is follows the file right after core commits (search_index.py); until then search leaves it out, and the
hourly repair fixes whatever a crash left behind."""

import threading

import pytest

from mnemerynne import db, fs, retrieval, search_index, users
from mnemerynne.ingest import store
from test_fs import _add, _drain, _found, _item, embedder, world  # noqa: F401


def _chunks(conn, file_id):
    return {tuple(r) for r in conn.execute("SELECT collection_id, folder_id, claude_hidden FROM chunks WHERE file_id = ?",
                                           (file_id,))}


def _vectors(conn, file_id):
    return {tuple(r) for r in conn.execute("SELECT collection_id, folder_id, claude_hidden FROM chunk_vec WHERE rowid IN "
                                           "(SELECT id FROM chunks WHERE file_id = ?)", (file_id,))}


def test_the_index_moves_out_of_core_with_its_vectors_keyword_index_and_answer_sources(settings, tmp_path, embedder):
    core = db.connect(settings.db_path, features=False)               # a box from before Phase 6a: all in core
    db.migrate(core, settings.embed_dim)
    alice = users.create_user(core, "alice", "password123")
    inbox = core.execute("SELECT id FROM collections WHERE kind = 'private'").fetchone()[0]
    _add(core, settings, inbox, "pool.md", "The pool opens at 8am.")
    _drain(core, settings, embedder)
    chunk_id = core.execute("SELECT id FROM chunks").fetchone()[0]
    core.execute("INSERT INTO conversations(id, user_id, title) VALUES ('c1', ?, 'q')", (alice,))
    message = core.execute("INSERT INTO messages(conversation_id, role, content) VALUES ('c1', 'assistant', 'x')"
                           ).lastrowid
    core.execute("INSERT INTO message_sources(message_id, n, chunk_id) VALUES (?, 1, ?)", (message, chunk_id))
    rows = core.execute("SELECT count(*) FROM chunks").fetchone()[0]
    core.close()

    db.migrate_all(settings.db_path, settings.embed_dim)
    conn = db.connect(settings.db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM main.sqlite_master")}
    assert not {"chunks", "chunk_vec", "chunks_fts"} & tables             # gone from core...
    assert conn.execute("SELECT count(*) FROM search.chunks").fetchone()[0] == rows   # ...into search.db
    assert conn.execute("SELECT count(*) FROM search.chunk_vec").fetchone()[0] == rows
    assert conn.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'pool'").fetchone()[0] == 1
    assert conn.execute("SELECT value FROM search_meta WHERE key = 'embed_dim'").fetchone()[0] == "4"
    assert conn.execute("SELECT value FROM main.meta WHERE key = 'embed_dim'").fetchone() is None
    # The old answer keeps its source; the reference that can't cross files is cut, the row stays.
    assert conn.execute("SELECT chunk_id FROM message_sources").fetchone()[0] == chunk_id
    assert not [r for r in conn.execute("PRAGMA foreign_key_list(message_sources)") if r["table"] == "chunks"]
    # Search reads it through the read-only attachment, as every request does.
    settings.vector_max_distance = settings.vector_max_distance_no_keywords = 99.0
    assert "pool.md" in _found(conn, settings, embedder, alice, "pool")
    with pytest.raises(Exception, match="readonly"):
        conn.execute("DELETE FROM chunks")                                # writes go through search.db's own


def test_indexing_writes_search_db_and_a_chunk_id_is_never_used_twice(conn, settings, embedder, world):
    first = _add(conn, settings, world["inbox"], "pool.md", "The pool opens at 8am.")
    _drain(conn, settings, embedder)
    assert conn.execute("SELECT status FROM files WHERE id = ?", (first,)).fetchone()[0] == "ready"
    last = conn.execute("SELECT max(id) FROM chunks").fetchone()[0]
    store.delete_file(conn, settings, first)                              # its chunks go just after the commit
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM chunk_vec").fetchone()[0] == 0
    _add(conn, settings, world["inbox"], "audit.md", "The night audit runs at 2am.")
    _drain(conn, settings, embedder)
    assert conn.execute("SELECT min(id) FROM chunks").fetchone()[0] > last   # an old answer's source stays dead


def test_a_moved_file_takes_its_chunks_along_right_after_the_commit(conn, settings, embedder, world):
    file_id = _add(conn, settings, world["inbox"], "pool.md", "The pool opens at 8am.")
    _drain(conn, settings, embedder)
    archive = fs.create_folder(conn, world["a"], world["oracle"], None, "Archive")
    [result] = fs.move(conn, world["a"], [_item("file", file_id)], world["oracle"], archive)
    assert result.ok
    assert _chunks(conn, file_id) == _vectors(conn, file_id) == {(world["oracle"], archive, 0)}
    assert "pool.md" in _found(conn, settings, embedder, world["bob"], "pool")

    fs.trash(conn, world["a"], [_item("folder", archive)])                 # the bin: no search finds it
    assert _chunks(conn, file_id) == {(fs.TRASH_COLLECTION, archive, 0)}
    assert "pool.md" not in _found(conn, settings, embedder, world["alice"], "pool")


def test_a_change_that_rolls_back_leaves_the_chunks_alone(conn, settings, embedder, world):
    file_id = _add(conn, settings, world["inbox"], "pool.md", "The pool opens at 8am.")
    _drain(conn, settings, embedder)
    before = _chunks(conn, file_id)
    with pytest.raises(RuntimeError):
        with db.transaction(conn):
            conn.execute("UPDATE files SET claude_hidden = 1 WHERE id = ?", (file_id,))
            search_index.sync(conn, [file_id])
            raise RuntimeError("something failed after")
    assert _chunks(conn, file_id) == before and db._after.get(id(conn)) is None


def test_hiding_from_claude_follows_and_search_never_trusts_a_stale_copy(conn, settings, embedder, world):
    file_id = _add(conn, settings, world["inbox"], "pool.md", "The pool opens at 8am.")
    _drain(conn, settings, embedder)
    fs.set_file_claude(conn, world["a"], file_id, visible=False)
    assert _chunks(conn, file_id) == _vectors(conn, file_id) == {(world["inbox"], 0, 1)}
    fs.set_file_claude(conn, world["a"], file_id, visible=True)

    # A copy that didn't catch up (a crash between the two commits): the file says hidden, its chunks don't.
    conn.execute("UPDATE files SET claude_hidden = 1 WHERE id = ?", (file_id,))
    chunk_ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE file_id = ?", (file_id,))]
    assert retrieval._load_hits(conn, chunk_ids)                                      # citations still load it
    assert retrieval._load_hits(conn, chunk_ids, current=True) == {}                  # search leaves it out
    assert "pool.md" not in _found(conn, settings, embedder, world["alice"], "pool")
    assert search_index.repair(str(settings.db_path)) == 1                           # the hourly repair
    assert _chunks(conn, file_id) == {(world["inbox"], 0, 1)}
    assert search_index.repair(str(settings.db_path)) == 0


def test_chunks_of_a_file_gone_without_the_after_step_are_swept(conn, settings, embedder, world):
    file_id = _add(conn, settings, world["inbox"], "pool.md", "The pool opens at 8am.")
    _drain(conn, settings, embedder)
    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))            # no cascade into search.db
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] > 0
    assert "pool.md" not in _found(conn, settings, embedder, world["alice"], "pool")   # and no error either
    assert db.sweep_orphans(settings.db_path).get("search.chunks")
    assert conn.execute("SELECT count(*) FROM chunk_vec").fetchone()[0] == 0          # vectors followed


def test_move_back_puts_the_index_into_core_for_the_older_code(conn, settings, embedder, world):
    file_id = _add(conn, settings, world["inbox"], "pool.md", "The pool opens at 8am.")
    _drain(conn, settings, embedder)
    rows = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    conn.close()
    copied = db.move_back(settings.db_path, "search")
    assert copied == {"chunks": rows, "chunk_vec": rows}
    assert not db.feature_path(settings.db_path, "search").exists()
    core = db.connect(settings.db_path, features=False)
    assert core.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'pool'").fetchone()[0] == 1
    assert core.execute("SELECT value FROM meta WHERE key = 'embed_dim'").fetchone()[0] == "4"
    db.migrate(core, settings.embed_dim)                                  # the older code's start: consistent
    core.execute("DELETE FROM chunks")                                    # its triggers came back too
    assert core.execute("SELECT count(*) FROM chunk_vec").fetchone()[0] == 0
    core.close()
    db.migrate_all(settings.db_path, settings.embed_dim)                  # and the newer code moves it out again
    assert db.feature_path(settings.db_path, "search").exists()


def test_two_processes_starting_together_migrate_one_at_a_time(settings):
    errors = []

    def start():
        try:
            db.migrate_all(settings.db_path, settings.embed_dim)
        except Exception as exc:                                          # pragma: no cover - the failure we guard
            errors.append(exc)
    threads = [threading.Thread(target=start) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    conn = db.connect(settings.db_path)
    assert conn.execute("SELECT count(*) FROM search_meta WHERE key = 'embed_dim'").fetchone()[0] == 1
