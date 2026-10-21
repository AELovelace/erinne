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


# Collections the user may search from Claude: their inbox if its switch is
# on, and libraries whose switch is on, reached through a grant (direct or
# group) that allows Claude.
_CLAUDE_SQL = """
    SELECT id FROM collections WHERE kind = 'private' AND owner_user_id = :u AND claude_enabled = 1
    UNION
    SELECT g.collection_id
      FROM collection_grants g
      JOIN collections c ON c.id = g.collection_id
     WHERE c.claude_enabled = 1 AND g.can_claude = 1
       AND (g.user_id = :u OR g.group_id IN (SELECT group_id FROM group_members WHERE user_id = :u))
"""


def readable_collection_ids(conn: sqlite3.Connection, user_id: int) -> set[int]:
    return {row[0] for row in conn.execute(_READABLE_SQL, {"u": user_id})}


def writable_collection_ids(conn: sqlite3.Connection, user_id: int) -> set[int]:
    return {row[0] for row in conn.execute(_WRITABLE_SQL, {"u": user_id})}


def claude_collection_ids(conn: sqlite3.Connection, user_id: int) -> set[int]:
    """Collections `user_id` may search from Claude; the MCP endpoint must
    search only these (always a subset of readable_collection_ids)."""
    return {row[0] for row in conn.execute(_CLAUDE_SQL, {"u": user_id})}


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


def _grant(conn: sqlite3.Connection, collection_id: int, column: str, principal_id: int,
           can_write: bool, can_claude: bool | None) -> None:
    _require_library(conn, collection_id)
    claude = None if can_claude is None else int(can_claude)
    conn.execute(
        f"INSERT INTO collection_grants(collection_id, {column}, can_write, can_claude) "
        f"VALUES (?, ?, ?, COALESCE(?, 1)) "
        f"ON CONFLICT(collection_id, {column}) WHERE {column} IS NOT NULL "
        f"DO UPDATE SET can_write = excluded.can_write, can_claude = COALESCE(?, can_claude)",
        (collection_id, principal_id, int(can_write), claude, claude),
    )


def grant_user(conn: sqlite3.Connection, collection_id: int, user_id: int, *, can_write: bool = False,
               can_claude: bool | None = None) -> None:
    """Grant (or update) a user's access to a library. `can_claude=None` keeps
    the grant's current Claude setting (on for a new grant)."""
    _grant(conn, collection_id, "user_id", user_id, can_write, can_claude)


def grant_group(conn: sqlite3.Connection, collection_id: int, group_id: int, *, can_write: bool = False,
                can_claude: bool | None = None) -> None:
    _grant(conn, collection_id, "group_id", group_id, can_write, can_claude)


def set_claude_enabled(conn: sqlite3.Connection, collection_id: int, enabled: bool, by_user_id: int) -> None:
    """Switch Claude search on or off for a whole library or one inbox."""
    if conn.execute("SELECT 1 FROM collections WHERE id = ?", (collection_id,)).fetchone() is None:
        raise ValueError(f"Collection {collection_id} does not exist.")
    conn.execute(
        "UPDATE collections SET claude_enabled = ?, claude_changed_by = ?, claude_changed_at = datetime('now') "
        "WHERE id = ?", (int(enabled), by_user_id, collection_id))


def revoke_user(conn: sqlite3.Connection, collection_id: int, user_id: int) -> None:
    conn.execute(
        "DELETE FROM collection_grants WHERE collection_id = ? AND user_id = ?", (collection_id, user_id)
    )


def revoke_group(conn: sqlite3.Connection, collection_id: int, group_id: int) -> None:
    conn.execute(
        "DELETE FROM collection_grants WHERE collection_id = ? AND group_id = ?", (collection_id, group_id)
    )


# -------------------------------------------------------------------- groups
def create_group(conn: sqlite3.Connection, name: str, member_ids: list[int] = ()) -> int:
    with transaction(conn):
        group_id = conn.execute("INSERT INTO groups(name) VALUES (?)", (name.strip(),)).lastrowid
        conn.executemany(
            "INSERT INTO group_members(group_id, user_id) VALUES (?, ?)", [(group_id, u) for u in member_ids]
        )
    return group_id


def set_group_members(conn: sqlite3.Connection, group_id: int, member_ids: list[int]) -> None:
    """Replace a group's members with exactly `member_ids`."""
    with transaction(conn):
        conn.execute("DELETE FROM group_members WHERE group_id = ?", (group_id,))
        conn.executemany("INSERT INTO group_members(group_id, user_id) VALUES (?, ?)",
                         [(group_id, u) for u in dict.fromkeys(member_ids)])


def group_member_ids(conn: sqlite3.Connection, group_id: int) -> list[int]:
    return [r[0] for r in conn.execute("SELECT user_id FROM group_members WHERE group_id = ?", (group_id,))]


# ------------------------------------------------------------------ overview
def access_summary(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Every collection a user can reach (inbox first, then libraries), with the
    strongest access, whether they may use it from Claude, and what gives it
    ('own inbox', 'direct' or group names)."""
    inbox = conn.execute("SELECT id, name, claude_enabled FROM collections "
                         "WHERE kind = 'private' AND owner_user_id = ?", (user_id,)).fetchone()
    summary = [{"id": inbox["id"], "name": inbox["name"], "kind": "private", "access": "write",
                "claude": bool(inbox["claude_enabled"]), "via": ["own inbox"]}] if inbox else []
    rows = conn.execute(
        "SELECT c.id, c.name, c.claude_enabled, g.can_write, g.can_claude, gr.name AS via_group "
        "FROM collection_grants g JOIN collections c ON c.id = g.collection_id "
        "LEFT JOIN groups gr ON gr.id = g.group_id "
        "WHERE g.user_id = :u OR g.group_id IN (SELECT group_id FROM group_members WHERE user_id = :u) "
        "ORDER BY c.name COLLATE NOCASE, gr.name IS NOT NULL, gr.name COLLATE NOCASE",
        {"u": user_id}).fetchall()
    libraries: dict[int, dict] = {}
    for r in rows:
        lib = libraries.setdefault(r["id"], {"id": r["id"], "name": r["name"], "kind": "library",
                                             "access": "read", "claude": False, "via": []})
        if r["can_write"]:
            lib["access"] = "write"
        lib["claude"] = lib["claude"] or bool(r["claude_enabled"] and r["can_claude"])
        lib["via"].append(r["via_group"] or "direct")
    return summary + list(libraries.values())


def _require_library(conn: sqlite3.Connection, collection_id: int) -> None:
    row = conn.execute("SELECT kind FROM collections WHERE id = ?", (collection_id,)).fetchone()
    if row is None:
        raise ValueError(f"Collection {collection_id} does not exist.")
    if row["kind"] != "library":
        # Private inboxes are never shared; publish a library instead.
        raise ValueError("Only libraries can be granted; private inboxes stay private.")
