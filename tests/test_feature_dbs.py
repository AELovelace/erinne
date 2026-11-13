"""A SQLite file per feature (mnemerynne/db.py, docs/plans/services-rewrite.md): its own write lock, so the
services write in parallel, with the core database attached read-only for joins."""

import sqlite3
import threading

import pytest

from mnemerynne import db, users

TOYS = """CREATE TABLE toys (
    id      INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,                  -- a user in core: kept by the code, not a reference
    name    TEXT NOT NULL
);
CREATE TABLE toy_parts (
    id     INTEGER PRIMARY KEY,
    toy_id INTEGER NOT NULL REFERENCES toys(id) ON DELETE CASCADE,
    part   TEXT NOT NULL
);
CREATE INDEX toy_parts_toy ON toy_parts(toy_id);
"""


@pytest.fixture
def core_path(tmp_path):
    path = tmp_path / "data" / "mnemerynne.db"
    conn = db.connect(path)
    db.migrate(conn, 4)
    users.create_user(conn, "jane", "password123")
    yield path, conn                                   # kept open, as the web app keeps core open
    conn.close()


@pytest.fixture
def toys(tmp_path, monkeypatch):
    """A made-up feature, "toys", with its migrations in a folder of its own."""
    folder = tmp_path / "migrations-toys"
    folder.mkdir()
    (folder / "0001_toys.sql").write_text(TOYS, encoding="utf-8")
    monkeypatch.setitem(db.FEATURE_DBS, "toys", db.FeatureDb("toys", moves=("toys", "toy_parts"), migrations=folder))
    return folder


def _old_toys_in_core(core):
    """The tables as they were in core before the split (with a real reference to users)."""
    core.executescript("""
        CREATE TABLE toys (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL);
        CREATE TABLE toy_parts (id INTEGER PRIMARY KEY, toy_id INTEGER NOT NULL REFERENCES toys(id), part TEXT NOT NULL);
        INSERT INTO toys(id, user_id, name) VALUES (1, 1, 'kite'), (2, 1, 'yoyo');
        INSERT INTO toy_parts(toy_id, part) VALUES (1, 'string'), (1, 'tail'), (2, 'string');
    """)


# ---------------------------------------------------------------- connections
def test_a_feature_connection_reads_core_by_plain_names_and_can_not_write_it(core_path, toys):
    path, _ = core_path
    conn = db.connect_feature(path, "toys")
    try:
        db.migrate_feature(conn, "toys")
        assert db.feature_path(path, "toys").exists()
        conn.execute("INSERT INTO toys(user_id, name) VALUES (1, 'kite')")
        row = conn.execute("SELECT t.name, u.username FROM toys t JOIN users u ON u.id = t.user_id").fetchone()
        assert tuple(row) == ("kite", "jane")                  # a join across the two files, by plain names
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE users SET username = 'mallory'")   # core is read-only from here
    finally:
        conn.close()


def test_a_feature_write_does_not_hold_cores_write_lock(core_path, toys):
    """The point of the split: writers in different files run at the same time."""
    path, core = core_path
    feature = db.connect_feature(path, "toys")
    db.migrate_feature(feature, "toys")
    feature.execute("BEGIN IMMEDIATE")                         # a feature write transaction, held open
    feature.execute("INSERT INTO toys(user_id, name) VALUES (1, 'kite')")
    other = db.connect(path)
    other.execute("PRAGMA busy_timeout = 200")                 # would give up fast if it had to wait
    try:
        users.create_user(other, "mina", "password123")        # takes core's write lock: still free
    finally:
        feature.execute("COMMIT")
        feature.close()
        other.close()
    assert core.execute("SELECT count(*) FROM users").fetchone()[0] == 2


def test_a_pool_can_open_feature_connections(core_path, toys):
    path, _ = core_path
    pool = db.Pool(db.feature_path(path, "toys"), opener=lambda: db.connect_feature(path, "toys"))
    conn = pool.get()
    try:
        names = {r["name"] for r in conn.execute("PRAGMA database_list")}
        assert names == {"main", "core"}
    finally:
        pool.put(conn)
        pool.close()


# ----------------------------------------------------------------- migrations
def test_feature_migrations_apply_once_and_count_on_their_own(core_path, toys):
    path, core = core_path
    conn = db.connect_feature(path, "toys")
    try:
        assert db.migrate_feature(conn, "toys") == 1
        assert db.migrate_feature(conn, "toys") == 1           # again: nothing to do
        (toys / "0002_colour.sql").write_text("ALTER TABLE toys ADD COLUMN colour TEXT;", encoding="utf-8")
        assert db.migrate_feature(conn, "toys") == 2
    finally:
        conn.close()
    assert db.schema_version(core) == len(db._migration_files())   # core's own version untouched


def test_feature_migrations_must_be_numbered_without_gaps(core_path, toys):
    (toys / "0003_skipped_two.sql").write_text("SELECT 1;", encoding="utf-8")
    conn = db.connect_feature(core_path[0], "toys")
    try:
        with pytest.raises(db.SchemaError, match="without gaps"):
            db.migrate_feature(conn, "toys")
    finally:
        conn.close()


# ------------------------------------------------------------- moving tables
def _ready(path):
    conn = db.connect_feature(path, "toys")
    db.migrate_feature(conn, "toys")
    conn.close()


def test_moving_copies_every_row_checks_and_then_drops_from_core(core_path, toys):
    path, core = core_path
    _old_toys_in_core(core)
    _ready(path)
    assert db.move_tables(core, "toys") == {"toys": 2, "toy_parts": 3}
    assert not db._has_table(core, "main", "toys") and not db._has_table(core, "main", "toy_parts")
    conn = db.connect_feature(path, "toys")
    try:
        assert [tuple(r) for r in conn.execute("SELECT id, name FROM toys ORDER BY id")] == [(1, "kite"), (2, "yoyo")]
        conn.execute("DELETE FROM toys WHERE id = 1")          # its own references still cascade, inside the file
        assert conn.execute("SELECT count(*) FROM toy_parts").fetchone()[0] == 1
    finally:
        conn.close()
    assert db.move_tables(core, "toys") == {}                  # again: already moved


def test_a_move_cut_short_is_done_again_without_doubles(core_path, toys):
    path, core = core_path
    _old_toys_in_core(core)
    _ready(path)
    conn = db.connect_feature(path, "toys")                     # a crash left a partial copy behind
    conn.execute("INSERT INTO toys(id, user_id, name) VALUES (1, 1, 'kite')")
    conn.close()
    assert db.move_tables(core, "toys") == {"toys": 2, "toy_parts": 3}
    conn = db.connect_feature(path, "toys")
    try:
        assert conn.execute("SELECT count(*) FROM toys").fetchone()[0] == 2
    finally:
        conn.close()


def test_a_move_is_refused_while_a_core_table_still_refers_to_it(core_path, toys):
    path, core = core_path
    _old_toys_in_core(core)
    core.execute("CREATE TABLE toy_reviews (id INTEGER PRIMARY KEY, toy_id INTEGER REFERENCES toys(id))")
    _ready(path)
    with pytest.raises(db.SchemaError, match="toy_reviews refers to it"):
        db.move_tables(core, "toys")
    assert db._has_table(core, "main", "toys")                 # nothing dropped


def test_a_move_is_refused_before_the_feature_has_its_tables(core_path, toys):
    path, core = core_path
    _old_toys_in_core(core)
    (toys / "0001_toys.sql").write_text("CREATE TABLE toys (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT);",
                                        encoding="utf-8")   # forgot toy_parts
    _ready(path)
    with pytest.raises(db.SchemaError, match="no table toy_parts"):
        db.move_tables(core, "toys")
    assert db._has_table(core, "main", "toys") and db._has_table(core, "main", "toy_parts")   # nothing dropped


# ------------------------------------------------ the web app migrates, services wait
def test_migrate_all_brings_core_and_each_feature_up_and_moves_their_tables(core_path, toys):
    path, core = core_path
    _old_toys_in_core(core)
    versions = db.migrate_all(path, 4)
    assert versions == {"core": len(db._migration_files()), "toys": 1}
    assert not db._has_table(core, "main", "toys")
    assert db.wait_migrated(path, timeout=1) == versions["core"]   # a service starts at once


def test_a_service_waits_for_the_web_app_and_gives_up_with_a_reason(core_path, toys):
    path, _ = core_path                                         # core migrated, but toys.db never made
    with pytest.raises(db.SchemaError, match="toys.db missing"):
        db.wait_migrated(path, timeout=0.5)

    def web_app_starts():                                       # ...until the web app migrates it
        db.migrate_all(path, 4)

    starter = threading.Timer(0.5, web_app_starts)
    starter.start()
    try:
        assert db.wait_migrated(path, timeout=10) == len(db._migration_files())
    finally:
        starter.join()


def test_a_database_newer_than_the_code_stops_a_service(core_path, toys):
    path, core = core_path
    core.execute(f"PRAGMA user_version = {len(db._migration_files()) + 1}")
    with pytest.raises(db.SchemaError, match="newer than this code"):
        db.wait_migrated(path, timeout=0.5)
