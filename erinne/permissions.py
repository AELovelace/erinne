"""Which collections a user may read or write.

Retrieval must pass `readable_collection_ids()` into the search queries
themselves (FTS and vector), never filter results afterwards.
"""

from __future__ import annotations

import sqlite3

from erinne.db import transaction

_READABLE_SQL = """
    SELECT id FROM collections WHERE kind = 'private' AND owner_user_id = :u
    UNION
    SELECT collection_id FROM collection_grants WHERE user_id = :u
    UNION
    SELECT g.collection_id
      FROM collection_grants g
      JOIN group_members m ON m.group_id = g.group_id
     WHERE m.user_id = :u
"""

_WRITABLE_SQL = """
    SELECT id FROM collections WHERE kind = 'private' AND owner_user_id = :u
    UNION
    SELECT collection_id FROM collection_grants WHERE user_id = :u AND can_write = 1
    UNION
    SELECT g.collection_id
      FROM collection_grants g
      JOIN group_members m ON m.group_id = g.group_id
     WHERE m.user_id = :u AND g.can_write = 1
"""


def readable_collection_ids(conn: sqlite3.Connection, user_id: int) -> set[int]:
    return {row[0] for row in conn.execute(_READABLE_SQL, {"u": user_id})}


def writable_collection_ids(conn: sqlite3.Connection, user_id: int) -> set[int]:
    return {row[0] for row in conn.execute(_WRITABLE_SQL, {"u": user_id})}


def private_collection_id(conn: sqlite3.Connection, user_id: int) -> int | None:
    row = conn.execute(
        "SELECT id FROM collections WHERE kind = 'private' AND owner_user_id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else None


def readable_collections(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """Collections for display: the user's inbox first, then libraries by name."""
    return conn.execute(
        f"SELECT c.id, c.kind, c.name FROM collections c WHERE c.id IN ({_READABLE_SQL}) "
        "ORDER BY c.kind = 'library', c.name COLLATE NOCASE",
        {"u": user_id},
    ).fetchall()


# ----------------------------------------------------------------- libraries
def create_library(conn: sqlite3.Connection, name: str) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("Library name must not be empty.")
    cur = conn.execute("INSERT INTO collections(kind, name) VALUES ('library', ?)", (name,))
    return cur.lastrowid


def get_library(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM collections WHERE kind = 'library' AND name = ? COLLATE NOCASE", (name.strip(),)
    ).fetchone()


def grant_user(conn: sqlite3.Connection, collection_id: int, user_id: int, *, can_write: bool = False) -> None:
    """Grant (or update) a user's access to a library."""
    _require_library(conn, collection_id)
    conn.execute(
        "INSERT INTO collection_grants(collection_id, user_id, can_write) VALUES (?, ?, ?) "
        "ON CONFLICT(collection_id, user_id) WHERE user_id IS NOT NULL "
        "DO UPDATE SET can_write = excluded.can_write",
        (collection_id, user_id, int(can_write)),
    )


def grant_group(conn: sqlite3.Connection, collection_id: int, group_id: int, *, can_write: bool = False) -> None:
    _require_library(conn, collection_id)
    conn.execute(
        "INSERT INTO collection_grants(collection_id, group_id, can_write) VALUES (?, ?, ?) "
        "ON CONFLICT(collection_id, group_id) WHERE group_id IS NOT NULL "
        "DO UPDATE SET can_write = excluded.can_write",
        (collection_id, group_id, int(can_write)),
    )


def revoke_user(conn: sqlite3.Connection, collection_id: int, user_id: int) -> None:
    conn.execute(
        "DELETE FROM collection_grants WHERE collection_id = ? AND user_id = ?", (collection_id, user_id)
    )


def create_group(conn: sqlite3.Connection, name: str, member_ids: list[int] = ()) -> int:
    with transaction(conn):
        group_id = conn.execute("INSERT INTO groups(name) VALUES (?)", (name.strip(),)).lastrowid
        conn.executemany(
            "INSERT INTO group_members(group_id, user_id) VALUES (?, ?)", [(group_id, u) for u in member_ids]
        )
    return group_id


def _require_library(conn: sqlite3.Connection, collection_id: int) -> None:
    row = conn.execute("SELECT kind FROM collections WHERE id = ?", (collection_id,)).fetchone()
    if row is None:
        raise ValueError(f"Collection {collection_id} does not exist.")
    if row["kind"] != "library":
        # Private inboxes are never shared; publish a library instead.
        raise ValueError("Only libraries can be granted; private inboxes stay private.")
