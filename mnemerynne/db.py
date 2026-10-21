"""SQLite access: connections, transactions, migrations and the vector table.

Everything lives in one database file: metadata, FTS5 keyword index,
sqlite-vec vectors and (later) LangGraph checkpoints and memories.

Connections run in autocommit mode (isolation_level=None) because LangGraph's
SQLite checkpointer and store manage their own transactions; our code opens
explicit transactions with `transaction()`.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import sqlite_vec

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class SchemaError(RuntimeError):
    """The database does not match what this version of the code expects."""


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a connection with the settings every part of the app relies on."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=10.0)
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


def _migration_files() -> list[tuple[int, Path]]:
    found = []
    for path in sorted(MIGRATIONS_DIR.iterdir()):
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
    files = _migration_files()
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
            broken = conn.execute("PRAGMA foreign_key_check").fetchall()
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
    if ensure_vectors:
        ensure_vector_table(conn, embed_dim)
    return schema_version(conn)


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
VEC_LAYOUT = 2


def _create_vector_table(conn: sqlite3.Connection, embed_dim: int) -> None:
    # Metadata columns are filtered inside the KNN query. vec0 metadata can't
    # be NULL, so folder 0 means the collection root.
    conn.execute(
        f"CREATE VIRTUAL TABLE chunk_vec USING vec0("
        f"embedding float[{int(embed_dim)}] distance_metric=cosine, "
        f"collection_id integer, folder_id integer)"
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
            "INSERT INTO chunk_vec(rowid, embedding, collection_id, folder_id) "
            "SELECT v.id, v.embedding, c.collection_id, c.folder_id FROM temp.vec_copy v JOIN chunks c ON c.id = v.id"
        )
        conn.execute("DROP TABLE temp.vec_copy")
