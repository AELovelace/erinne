"""SQLite access: connections, transactions, migrations and the vector table.

The core database file holds users, files, folders, permissions, search and settings. Features move into
files of their own, one per feature (games.db, rooms.db, ...: FEATURE_DBS), so each has its own write lock and
the services write in parallel (docs/plans/services-rewrite.md). A feature's connection sees core too,
attached read-only as `core`, so its queries can still join users or files. Request connections (the pools) also
attach every other feature file read-only under its own name (attach_features), so any code can *read* any table
by its plain name wherever it lives; a write must go through the owner's connection, and a stray one fails loudly
("attempt to write a readonly database").

Connections run in autocommit mode (isolation_level=None) because LangGraph's
SQLite checkpointer and store manage their own transactions; our code opens
explicit transactions with `transaction()`.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Sequence

import sqlite_vec

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class SchemaError(RuntimeError):
    """The database does not match what this version of the code expects."""


def connect(path: Path | str, *, features: bool = False) -> sqlite3.Connection:
    """Open a connection with the settings every part of the app relies on. `features`: also attach every feature
    file read-only (attach_features), for request connections that read across them (the pools)."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=10.0,
                           uri=features)                  # uri: so ATTACH takes the feature files' ?mode=ro
    _configure(conn, path)
    if features and str(path) != ":memory:":
        attach_features(conn, path)
    return conn


def attach_features(conn: sqlite3.Connection, core_path: Path | str, skip: str | None = None) -> None:
    """Attach each feature file that exists, read-only, under the feature's name (games, rooms, ...): unqualified
    names find their tables (SQLite looks in main, then the attached files in order). Read-only, so BEGIN IMMEDIATE
    on this connection never waits for those files' write locks, and a write to one fails instead of landing in
    the wrong transaction. A file made after the connection opened isn't seen by it (the pools open theirs after
    the web app's migrations, and the services wait for those)."""
    for name in FEATURE_DBS:
        path = feature_path(core_path, name)
        if name == skip or not path.exists():
            continue
        conn.execute(f'ATTACH DATABASE ? AS "{name}"', (path.resolve().as_uri() + "?mode=ro",))


@dataclass(frozen=True)
class FeatureDb:
    """A feature's own database file (<data dir>/<name>.db), next to the core one."""
    name: str
    moves: tuple[str, ...] = ()         # core tables whose rows move here (move_tables), in this order
    migrations: Path | None = None      # its numbered migrations; default migrations/<name>/
    # Its columns holding a core user's or file's id, as (table, column). SQLite can't follow a reference into
    # another file, so these are what forget_user and sweep_orphans clean when that user or file goes.
    user_columns: tuple[tuple[str, str], ...] = ()
    file_columns: tuple[tuple[str, str], ...] = ()
    # Columns that keep their row when that user goes, only losing the name (core's ON DELETE SET NULL): a
    # message stays in its room, from "someone who has left".
    user_null_columns: tuple[tuple[str, str], ...] = ()


# The feature files, filled phase by phase (docs/plans/services-rewrite.md): each with its migrations under
# migrations/<name>/ and the core tables that move into it.
GAMES_TABLES = ("coin_accounts", "coin_transactions", "lumi_pets", "lumi_gardens", "lumi_runs", "lumi_battles",
                "lumi_push_events", "lumi_parties", "lumi_party_members", "push_subscriptions", "arcade_rooms",
                "arcade_runs", "casino_rooms", "prism_runs", "runner_runs", "lumisoft_progress", "lumisoft_bests",
                "lumisoft_runs")
ROOMS_TABLES = ("chat_rooms", "chat_members", "chat_messages", "chat_reactions", "chat_attachments", "chat_mentions",
                "chat_events")
FEATURE_DBS: dict[str, FeatureDb] = {f.name: f for f in (
    # Phase 2: the built-in games, their coins and Lumi's reminders (web/games.py, the games service). A new games
    # table goes in migrations/games/, never in core's migrations.
    FeatureDb("games", moves=GAMES_TABLES,
              user_columns=tuple((t, "user_id") for t in GAMES_TABLES
                                 if t not in ("coin_transactions", "lumi_parties", "arcade_rooms", "casino_rooms"))),
    # Phase 3: Rooms (rooms.py, rooms_bus.py, web/rooms.py, the rooms service). A message outlives its author.
    FeatureDb("rooms", moves=ROOMS_TABLES,
              user_columns=(("chat_members", "user_id"), ("chat_reactions", "user_id"), ("chat_mentions", "user_id")),
              user_null_columns=(("chat_rooms", "created_by"), ("chat_messages", "user_id"),
                                 ("chat_messages", "pinned_by"), ("chat_attachments", "user_id"))),
)}


@contextmanager
def table_connection(core: sqlite3.Connection, feature: str, table: str) -> Iterator[sqlite3.Connection | None]:
    """A connection that holds `table`: core itself while the feature's tables are still there (a database only
    core's migrations ran on: the worker before the web app's first start, many tests), else the feature's own
    file, opened for the block. None when neither has it yet. For code outside a request (the worker's sweeps)."""
    if _has_table(core, "main", table):
        yield core
        return
    core_path = core.execute("PRAGMA database_list").fetchone()[2]
    if not core_path or not feature_path(core_path, feature).exists():
        yield None
        return
    conn = connect_feature(core_path, feature)
    try:
        yield conn if _has_table(conn, "main", table) else None
    finally:
        conn.close()


def connect_for(core_path: Path | str, feature: str, table: str) -> sqlite3.Connection:
    """A new connection that holds `table`, from the core database's path: the feature's file once its tables have
    moved there, else core (code that keeps a path, not a connection: rooms_bus's streams). The caller closes it."""
    if feature_path(core_path, feature).exists():
        conn = connect_feature(core_path, feature)
        if _has_table(conn, "main", table):
            return conn
        conn.close()
    return connect(core_path)


def feature_path(core_path: Path | str, feature: str) -> Path:
    """games -> <the core database's folder>/games.db"""
    return Path(core_path).with_name(f"{feature}.db")


def connect_feature(core_path: Path | str, feature: str) -> sqlite3.Connection:
    """A connection to a feature's own file, with the core database attached read-only as `core`: queries find
    core's tables by their plain names (JOIN users), and a write to one fails ("readonly database") instead of
    spreading a transaction over two files.

    Read-only for speed, not only safety: BEGIN IMMEDIATE takes the write lock on every writable database a
    connection has attached, so a writable core would put every feature write back in line for core's lock."""
    path = feature_path(core_path, feature)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=10.0,
                           uri=True)                          # uri: so ATTACH takes core's ?mode=ro
    _configure(conn, path)
    conn.execute("ATTACH DATABASE ? AS core", (Path(core_path).resolve().as_uri() + "?mode=ro",))
    attach_features(conn, core_path, skip=feature)             # the other features' tables, read-only too
    return conn


def _configure(conn: sqlite3.Connection, path: Path | str) -> sqlite3.Connection:
    """The settings every connection gets (rows by name, foreign keys, waits for the lock, WAL)."""
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


class Pool:
    """Open connections for the web app's requests to reuse, so a page doesn't open the database (and lose its
    cache of recently read pages) each time. It never makes anyone wait: with none spare, get() opens one
    more, because event streams and downloads keep theirs for as long as they run."""

    def __init__(self, path: Path | str, keep: int = 8,
                 opener: Callable[[], sqlite3.Connection] | None = None) -> None:
        """`opener`: how to open a new connection (a feature's: lambda: connect_feature(core, "games"));
        default connect(path)."""
        self.path, self.keep = path, keep
        self._open = opener or (lambda: connect(self.path, features=True))   # requests read across the files
        self._idle: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._closed = False

    def get(self) -> sqlite3.Connection:
        with self._lock:
            if self._idle:
                return self._idle.pop()
        return self._open()

    def put(self, conn: sqlite3.Connection) -> None:
        """Back for the next request; closed instead if it is mid-transaction, surplus or the app is stopping."""
        try:
            reusable = not conn.in_transaction
        except sqlite3.ProgrammingError:
            return                                     # a handler closed it already
        with self._lock:
            if reusable and not self._closed and len(self._idle) < self.keep:
                self._idle.append(conn)
                return
        conn.close()

    def close(self) -> None:
        """App shutdown: close the spares; ones still in use close as they come back."""
        with self._lock:
            self._closed = True
            idle, self._idle = self._idle, []
        for conn in idle:
            conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE … COMMIT, rolling back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# (db file, collection) -> (version, rows by id, what's been worked out from those rows: name -> value)
_folder_cache: dict[tuple[str, int], tuple[int, dict[int, sqlite3.Row], dict]] = {}
_folder_cache_lock = threading.Lock()


def folder_derived(conn: sqlite3.Connection, collection_id: int, name: str, make):
    """Something worked out from a collection's folders, `make(conn, rows)`, kept with those rows until its folders
    change (folder_rows): the name order, which folders are live, which are under the Agents folder. On the box
    these were worked out again for every request from all 28,789 folders. The value is shared by every request:
    make it immutable (a frozenset, a tuple, a dict nobody changes)."""
    rows = folder_rows(conn, collection_id)
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    entry = _folder_cache.get((path, collection_id)) if path else None
    if entry is None or entry[1] is not rows:      # an in-memory database, or rows replaced meanwhile
        return make(conn, rows)
    derived = entry[2]
    if name not in derived:
        derived[name] = make(conn, rows)            # two requests may both work it out once: same answer
    return derived[name]


def folder_rows(conn: sqlite3.Connection, collection_id: int) -> dict[int, sqlite3.Row]:
    """Every folder row of a collection (SELECT *), by id: read from the database only when its folders changed
    since the last read (folder_versions: a new random version on every change, set by triggers whichever
    process wrote), otherwise from memory.
    On the box every request re-read the 28,789 rows of the big library, a dozen requests at once, and they
    queued behind each other. Read-only: callers must not change the dict or its rows.

    The version is read before the rows, so rows read during a change are at worst newer than their version,
    and are simply read again next time."""
    found = conn.execute("SELECT version FROM folder_versions WHERE collection_id = ?", (collection_id,)).fetchone()
    version = found[0] if found else 0
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    if not path:                                   # an in-memory database (tests): no file to tell them apart by
        return {r["id"]: r for r in conn.execute("SELECT * FROM folders WHERE collection_id = ?", (collection_id,))}
    key = (path, collection_id)                    # this database file's collection
    cached = _folder_cache.get(key)
    if cached is not None and cached[0] == version:
        return cached[1]
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM folders WHERE collection_id = ?", (collection_id,))}
    with _folder_cache_lock:
        _folder_cache[key] = (version, rows, {})   # nothing worked out from the new rows yet (folder_derived)
    return rows


@contextmanager
def read_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN (deferred) … COMMIT: a consistent read that doesn't take the write lock. A write inside upgrades it;
    if another writer got in first, SQLite refuses ("database is locked") rather than overwrite their change."""
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _migration_files(directory: Path | None = None) -> list[tuple[int, Path]]:
    """The numbered migrations in a folder (core's: migrations/; a feature's: migrations/<name>/)."""
    directory = directory or MIGRATIONS_DIR
    if not directory.is_dir():
        return []                                      # a feature with no migrations yet
    found = []
    for path in sorted(directory.iterdir()):
        match = _MIGRATION_RE.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    numbers = [n for n, _ in found]
    if numbers != list(range(1, len(numbers) + 1)):
        raise SchemaError(f"Migrations must be numbered 0001.. without gaps, found {numbers}")
    return found


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection, embed_dim: int, *, ensure_vectors: bool = True) -> int:
    """Apply pending migrations, then make sure the vector table exists.

    Returns the schema version after migrating. `ensure_vectors=False` skips
    the vector-table check (used by re-embedding, which rebuilds it).
    """
    _apply_migrations(conn, _migration_files(), "foreign_key_check")
    if ensure_vectors:
        ensure_vector_table(conn, embed_dim)
    return schema_version(conn)


def _apply_migrations(conn: sqlite3.Connection, files: list[tuple[int, Path]], fk_check: str) -> None:
    """Apply the files newer than the connection's main database's user_version, each in its own transaction
    with the version bump. `fk_check`: the PRAGMA that checks references afterwards (a feature's checks only its
    own file, not the read-only core it has attached)."""
    current = schema_version(conn)
    if current > len(files):
        raise SchemaError(
            f"Database schema v{current} is newer than this code (v{len(files)}); upgrade mnemerynne."
        )
    for number, path in files:
        if number <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        # Table rebuilds (create new, copy, drop old, rename) need foreign keys
        # off; that can only change outside a transaction.
        no_fk = sql.startswith("-- mnemerynne:no-fk")
        if no_fk:
            conn.execute("PRAGMA foreign_keys = OFF")
        # executescript() commits any open transaction, so wrap the script and
        # the version bump in one explicit transaction of our own.
        try:
            conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {number};")
            broken = conn.execute(f"PRAGMA {fk_check}").fetchall()
            if broken:
                raise SchemaError(f"Migration {path.name} left {len(broken)} broken reference(s), "
                                  f"e.g. {tuple(broken[0])}")
            conn.execute("COMMIT")
        except (sqlite3.Error, SchemaError) as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            if isinstance(exc, SchemaError):
                raise
            raise SchemaError(f"Migration {path.name} failed: {exc}") from exc
        finally:
            if no_fk:
                conn.execute("PRAGMA foreign_keys = ON")


# ------------------------------------------------------------- feature files
def _feature(feature: str) -> FeatureDb:
    try:
        return FEATURE_DBS[feature]
    except KeyError:
        raise SchemaError(f"No feature database {feature!r}; there are: {', '.join(FEATURE_DBS) or 'none yet'}")


def _feature_files(feature: str) -> list[tuple[int, Path]]:
    found = _feature(feature)
    return _migration_files(found.migrations or MIGRATIONS_DIR / found.name)


def migrate_feature(conn: sqlite3.Connection, feature: str) -> int:
    """Apply a feature file's pending migrations on its connection (connect_feature). Returns its version.
    Its tables may not reference core's (users, files): SQLite only follows references inside one file, so
    those links are kept by the code (deletion events), not by the database."""
    _apply_migrations(conn, _feature_files(feature), "main.foreign_key_check")
    return schema_version(conn)


def _has_table(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    return conn.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?",
                        (table,)).fetchone() is not None


def move_tables(core: sqlite3.Connection, feature: str) -> dict[str, int]:
    """Move a feature's tables (FeatureDb.moves) from the core database into its own file, on a core connection.
    Its migrations must have made the tables there. Safe to run again: a table already moved is skipped, and a
    copy left half done (a crash) is done again. Rows are counted on both sides before anything is dropped, and
    a table still referenced by one that stays in core is refused. Returns the rows moved per table."""
    found = _feature(feature)
    pending = [t for t in found.moves if _has_table(core, "main", t)]
    if not pending:
        return {}
    staying = [r[0] for r in core.execute("SELECT name FROM main.sqlite_master WHERE type = 'table'")
               if r[0] not in pending]
    for table in staying:                              # a reference into a moving table would break in core
        for ref in core.execute(f'PRAGMA main.foreign_key_list("{table}")').fetchall():
            if ref["table"] in pending:
                raise SchemaError(f"Can't move {ref['table']} to {feature}.db: core's {table} refers to it.")
    core_path = core.execute("PRAGMA database_list").fetchone()[2]
    core.execute("ATTACH DATABASE ? AS feature", (str(feature_path(core_path, feature)),))
    moved: dict[str, int] = {}
    try:
        for table in pending:
            if not _has_table(core, "feature", table):
                raise SchemaError(f"{feature}.db has no table {table}: add it to migrations/{feature}/ first.")
            theirs = {r["name"] for r in core.execute(f"PRAGMA feature.table_info({table})")}
            columns = ", ".join(f'"{r["name"]}"' for r in core.execute(f"PRAGMA main.table_info({table})")
                                if r["name"] in theirs)
            with transaction(core):                    # the copy, all or nothing
                core.execute(f"DELETE FROM feature.{table}")          # anything a crashed copy left
                core.execute(f"INSERT INTO feature.{table} ({columns}) SELECT {columns} FROM main.{table}")
                here = core.execute(f"SELECT count(*) FROM main.{table}").fetchone()[0]
                there = core.execute(f"SELECT count(*) FROM feature.{table}").fetchone()[0]
                if here != there:
                    raise SchemaError(f"Moving {table}: {here} rows in core but {there} in {feature}.db")
            moved[table] = here
        core.execute("PRAGMA foreign_keys = OFF")      # the moving tables refer to each other: drop in any order
        try:
            with transaction(core):                    # core gives them up only once every copy checked out
                for table in pending:
                    core.execute(f"DROP TABLE main.{table}")
        finally:
            core.execute("PRAGMA foreign_keys = ON")
    finally:
        core.execute("DETACH DATABASE feature")
    return moved


def move_back(core_path: Path | str, feature: str) -> dict[str, int]:
    """The way back from a phase (docs/plans/services-rewrite.md: A/B on the box): copy a feature's tables from its
    own file back into core, for the code from before that phase. Rows are counted on both sides; then the
    feature file is set aside as <name>.moved-back-<time>.db (with its -wal/-shm), never deleted, so a
    migrate_all later (the newer code again) makes a fresh one and moves the rows out once more. Stop every
    process first. The tables come back without their references to core's users (they couldn't cross files),
    so until the newer code returns, deleting a user leaves their game rows for sweep_orphans to clean then.
    Returns the rows copied per table."""
    import time

    found = _feature(feature)
    path = feature_path(core_path, feature)
    if not path.exists():
        return {}
    core = connect(core_path)
    copied: dict[str, int] = {}
    try:
        core.execute("ATTACH DATABASE ? AS feature", (str(path),))
        try:
            here = [t for t in found.moves if _has_table(core, "feature", t)]
            clash = [t for t in here if _has_table(core, "main", t)]
            if clash:
                raise SchemaError(f"Core already has {', '.join(clash)}: nothing to move back.")
            with transaction(core):                    # all or nothing: the tables, their indexes, their rows
                for table in here:
                    sql = core.execute("SELECT sql FROM feature.sqlite_master WHERE type = 'table' AND name = ?",
                                       (table,)).fetchone()[0]
                    core.execute(sql)
                    for (index,) in core.execute("SELECT sql FROM feature.sqlite_master WHERE type = 'index' "
                                                 "AND tbl_name = ? AND sql IS NOT NULL", (table,)).fetchall():
                        core.execute(index)
                    core.execute(f"INSERT INTO main.{table} SELECT * FROM feature.{table}")
                    there = core.execute(f"SELECT count(*) FROM feature.{table}").fetchone()[0]
                    back = core.execute(f"SELECT count(*) FROM main.{table}").fetchone()[0]
                    if there != back:
                        raise SchemaError(f"Moving {table} back: {there} rows in {feature}.db but {back} in core")
                    copied[table] = back
        finally:
            core.execute("DETACH DATABASE feature")
    finally:
        core.close()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        part = Path(str(path) + suffix)
        if part.exists():
            part.rename(path.with_name(f"{feature}.moved-back-{stamp}.db{suffix}"))
    return copied


def _features_present(core_path: Path | str) -> list[FeatureDb]:
    return [f for f in FEATURE_DBS.values() if feature_path(core_path, f.name).exists()]


def forget_user(core_path: Path | str, user_id: int) -> int:
    """A user was deleted from core: delete their rows in every feature file (their pets, their coins, ...),
    since no reference crosses the files. Returns how many rows went. users.delete_user calls it."""
    gone = 0
    for found in _features_present(core_path):
        if not found.user_columns and not found.user_null_columns:
            continue
        conn = connect_feature(core_path, found.name)
        try:
            with transaction(conn):
                for table, column in found.user_columns:
                    gone += conn.execute(f"DELETE FROM main.{table} WHERE {column} = ?", (user_id,)).rowcount
                for table, column in found.user_null_columns:     # kept, without their name
                    gone += conn.execute(f"UPDATE main.{table} SET {column} = NULL WHERE {column} = ?",
                                         (user_id,)).rowcount
        finally:
            conn.close()
    return gone


def sweep_orphans(core_path: Path | str) -> dict[str, int]:
    """Delete feature rows whose user or file is no longer in core: what forget_user didn't catch (a file
    deleted inside someone else's transaction, a crash in between). The web app runs it at start;
    `mnemerynne check-orphans` on demand. Returns the rows deleted per "feature.table"."""
    deleted: dict[str, int] = {}
    for found in _features_present(core_path):
        checks = [(t, c, "core.users") for t, c in found.user_columns] + \
                 [(t, c, "core.files") for t, c in found.file_columns]
        if not checks and not found.user_null_columns:
            continue
        conn = connect_feature(core_path, found.name)
        try:
            with transaction(conn):
                for table, column, parent in checks:
                    n = conn.execute(f"DELETE FROM main.{table} WHERE {column} IS NOT NULL "
                                     f"AND {column} NOT IN (SELECT id FROM {parent})").rowcount
                    if n:
                        key = f"{found.name}.{table}"
                        deleted[key] = deleted.get(key, 0) + n
                for table, column in found.user_null_columns:        # kept, without the name of who left
                    n = conn.execute(f"UPDATE main.{table} SET {column} = NULL WHERE {column} IS NOT NULL "
                                     f"AND {column} NOT IN (SELECT id FROM core.users)").rowcount
                    if n:
                        key = f"{found.name}.{table}.{column}"
                        deleted[key] = deleted.get(key, 0) + n
        finally:
            conn.close()
    return deleted


def migrate_all(core_path: Path | str, embed_dim: int) -> dict[str, int]:
    """Everything, in order: core's migrations, then each feature file's, then their tables out of core. Only
    the web app (at start) and `mnemerynne init-db` run this; the services wait for it (wait_migrated), so two
    processes never apply the same migration. Returns each file's version ("core" for core)."""
    core = connect(core_path)
    try:
        versions = {"core": migrate(core, embed_dim)}
        for name in FEATURE_DBS:
            conn = connect_feature(core_path, name)
            try:
                versions[name] = migrate_feature(conn, name)
            finally:
                conn.close()
            moved = move_tables(core, name)
            if moved:
                logging.getLogger("mnemerynne.db").info("Moved into %s.db: %s.", name,
                                                        ", ".join(f"{t} ({n} rows)" for t, n in moved.items()))
    finally:
        core.close()
    return versions


def wait_migrated(core_path: Path | str, timeout: float = 120.0) -> int:
    """For a service: wait until the web app has brought core and every feature file up to this code's version
    (it migrates; the services don't), then return core's version. Still behind at the timeout, or newer than this
    code: SchemaError."""
    import time

    want_core = len(_migration_files())
    want = {name: len(_feature_files(name)) for name in FEATURE_DBS}
    until = time.monotonic() + timeout
    while True:
        core = connect(core_path)
        try:
            have_core = schema_version(core)
        finally:
            core.close()
        if have_core > want_core:
            raise SchemaError(f"Database schema v{have_core} is newer than this code (v{want_core}); "
                              "upgrade mnemerynne.")
        behind = [] if have_core >= want_core else [f"core v{have_core} < v{want_core}"]
        for name, version in want.items():
            if not feature_path(core_path, name).exists():
                behind.append(f"{name}.db missing")
                continue
            conn = connect_feature(core_path, name)
            try:
                have = schema_version(conn)
            finally:
                conn.close()
            if have < version:
                behind.append(f"{name} v{have} < v{version}")
        if not behind:
            return have_core
        if time.monotonic() >= until:
            raise SchemaError("The database isn't up to date (" + "; ".join(behind)
                              + "): the web app migrates it at start; is it running?")
        time.sleep(1)


def drop_vector_table(conn: sqlite3.Connection) -> None:
    """Remove chunk_vec so it can be rebuilt with another dimension."""
    with transaction(conn):
        conn.execute("DROP TRIGGER IF EXISTS chunks_vec_ad")
        conn.execute("DROP TABLE IF EXISTS chunk_vec")
        conn.execute("DELETE FROM meta WHERE key IN ('embed_dim', 'vec_layout')")


def ensure_vector_table(conn: sqlite3.Connection, embed_dim: int) -> None:
    """Create chunk_vec with the configured dimension, or check it matches.

    The vector size is fixed when the table is created, so it lives outside the
    numbered migrations. Changing embedder dimension requires a re-index
    (`mnemerynne reset-vectors`, added with the ingestion pipeline).
    """
    row = conn.execute("SELECT value FROM meta WHERE key = 'embed_dim'").fetchone()
    if row is not None:
        if int(row["value"]) != embed_dim:
            raise SchemaError(
                f"chunk_vec was built for {row['value']}-dim embeddings but MNEMERYNNE_EMBED_DIM="
                f"{embed_dim}; re-index or restore the previous embedder setting."
            )
        layout = conn.execute("SELECT value FROM meta WHERE key = 'vec_layout'").fetchone()
        if layout is None or int(layout["value"]) < VEC_LAYOUT:
            _upgrade_vector_table(conn, embed_dim)
        return
    with transaction(conn):
        _create_vector_table(conn, embed_dim)
        conn.execute("INSERT INTO meta(key, value) VALUES ('embed_dim', ?)", (str(embed_dim),))


# 2: vectors carry folder_id as well as collection_id (folder-scoped search).
# 3: and claude_hidden (files taken out of Claude's view; migration 0014).
VEC_LAYOUT = 3


def _create_vector_table(conn: sqlite3.Connection, embed_dim: int) -> None:
    # Metadata columns are filtered inside the KNN query. vec0 metadata can't
    # be NULL, so folder 0 means the collection root.
    conn.execute(
        f"CREATE VIRTUAL TABLE chunk_vec USING vec0("
        f"embedding float[{int(embed_dim)}] distance_metric=cosine, "
        f"collection_id integer, folder_id integer, claude_hidden integer)"
    )
    # Keep vectors in step with chunks, including cascaded deletes.
    conn.execute(
        "CREATE TRIGGER chunks_vec_ad AFTER DELETE ON chunks BEGIN "
        "DELETE FROM chunk_vec WHERE rowid = old.id; END"
    )
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('vec_layout', ?)", (str(VEC_LAYOUT),))


def _upgrade_vector_table(conn: sqlite3.Connection, embed_dim: int) -> None:
    """Rebuild chunk_vec in the current layout, keeping every embedding.

    vec0 tables can't be renamed, so vectors go through a temp table.
    """
    with transaction(conn):
        conn.execute("DROP TABLE IF EXISTS temp.vec_copy")
        conn.execute("CREATE TEMP TABLE vec_copy AS SELECT rowid AS id, embedding FROM chunk_vec")
        conn.execute("DROP TRIGGER IF EXISTS chunks_vec_ad")
        conn.execute("DROP TABLE chunk_vec")
        _create_vector_table(conn, embed_dim)
        # Scope comes from chunks, the source of truth.
        conn.execute(
            "INSERT INTO chunk_vec(rowid, embedding, collection_id, folder_id, claude_hidden) "
            "SELECT v.id, v.embedding, c.collection_id, c.folder_id, c.claude_hidden "
            "FROM temp.vec_copy v JOIN chunks c ON c.id = v.id"
        )
        conn.execute("DROP TABLE temp.vec_copy")
