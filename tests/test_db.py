import struct

import pytest

from erinne import db, users

LATEST = len(db._migration_files())


def _vec(*values):
    return struct.pack(f"{len(values)}f", *values)


def _add_file_with_chunk(conn, collection_id, text="Checkout time is 11am.", sha="a" * 64):
    conn.execute(
        "INSERT OR IGNORE INTO blobs(sha256, kind, size, rel_path) VALUES (?, 'pdf', 1, ?)", (sha, f"{sha}.pdf")
    )
    file_id = conn.execute(
        "INSERT INTO files(collection_id, blob_sha256, display_name) VALUES (?, ?, 'Guide.pdf')",
        (collection_id, sha),
    ).lastrowid
    chunk_id = conn.execute(
        "INSERT INTO chunks(file_id, collection_id, ord, page_start, page_end, section_path, text) "
        "VALUES (?, ?, 0, 1, 1, 'Front Desk > Checkout', ?)",
        (file_id, collection_id, text),
    ).lastrowid
    conn.execute(
        "INSERT INTO chunk_vec(rowid, embedding, collection_id) VALUES (?, ?, ?)",
        (chunk_id, _vec(1, 0, 0, 0), collection_id),
    )
    return file_id, chunk_id


def test_migrate_from_empty(conn, settings):
    assert db.schema_version(conn) == LATEST
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    assert {"users", "collections", "files", "chunks", "chunks_fts", "chunk_vec", "jobs", "messages"} <= tables
    assert conn.execute("SELECT value FROM meta WHERE key='embed_dim'").fetchone()[0] == "4"
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_is_idempotent(conn, settings):
    assert db.migrate(conn, settings.embed_dim) == LATEST


def test_embed_dim_mismatch_is_refused(conn):
    with pytest.raises(db.SchemaError, match="re-index"):
        db.migrate(conn, 1024)


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_init.sql").write_text(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n"
        "CREATE TABLE chunks (id INTEGER PRIMARY KEY);", encoding="utf-8")
    (migrations / "0002_broken.sql").write_text(
        "CREATE TABLE half_done (id INTEGER);\nTHIS IS NOT SQL;", encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", migrations)
    conn = db.connect(tmp_path / "t.db")
    with pytest.raises(db.SchemaError, match="0002_broken"):
        db.migrate(conn, 4)
    assert db.schema_version(conn) == 1
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='half_done'").fetchone()[0] == 0


def test_fts_and_vectors_follow_chunk_lifecycle(conn):
    users.create_user(conn, "alice", "correct horse")
    inbox = conn.execute("SELECT id FROM collections WHERE kind='private'").fetchone()[0]
    file_id, chunk_id = _add_file_with_chunk(conn, inbox)

    assert conn.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'checkout'").fetchone()[0] == chunk_id
    # Porter stemming: "times" matches "time".
    assert conn.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'times'").fetchone()[0] == 1

    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))  # cascades to chunks
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'checkout'").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM chunk_vec").fetchone()[0] == 0


def test_vector_search_filters_by_collection_inside_knn(conn):
    users.create_user(conn, "alice", "correct horse")
    users.create_user(conn, "bob", "battery staple")
    alice, bob = [r[0] for r in conn.execute("SELECT id FROM collections ORDER BY id")]
    _add_file_with_chunk(conn, alice, "Alice's private note.", sha="a" * 64)
    _add_file_with_chunk(conn, bob, "Bob's private note.", sha="b" * 64)

    hits = conn.execute(
        "SELECT rowid, collection_id FROM chunk_vec WHERE embedding MATCH ? AND k = 10 AND collection_id IN (?)",
        (_vec(1, 0, 0, 0), alice),
    ).fetchall()
    assert [h["collection_id"] for h in hits] == [alice]
