"""User accounts. Every user is created together with their private inbox."""

from __future__ import annotations

import sqlite3

from mnemerynne import auth, share
from mnemerynne.db import transaction

ROLES = ("user", "admin")


class UserExists(ValueError):
    pass


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password: str | None = None,
    *,
    role: str = "user",
    pw_hash: str | None = None,
) -> int:
    """Create a user and their private collection; returns the user id.

    Pass `pw_hash` instead of `password` to import an existing pbkdf2 hash.
    """
    name = auth.validate_username(username)
    if role not in ROLES:
        raise ValueError(f"Role must be one of {ROLES}.")
    if pw_hash is None:
        pw_hash = auth.hash_password(auth.validate_password(password or ""))

    with transaction(conn):
        try:
            cur = conn.execute(
                # New accounts follow the organisation's default theme ('' = no choice of their own; mnemerynne/theme.py);
                # the column default is still 'sakura' (0022).
                "INSERT INTO users(username, pw_hash, role, theme) VALUES (?, ?, ?, '')", (name, pw_hash, role)
            )
        except sqlite3.IntegrityError as exc:
            raise UserExists(f"Username '{name}' is already taken.") from exc
        user_id = cur.lastrowid
        inbox = conn.execute(
            "INSERT INTO collections(kind, name, owner_user_id) VALUES ('private', ?, ?)",
            (f"{name}'s inbox", user_id),
        ).lastrowid
        # With the SMB share on, the inbox is the folder Users/<name> there.
        share.attach_inbox(conn, inbox, name)
    return user_id


def get_user(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE username = ?", ((username or "").strip().lower(),)
    ).fetchone()


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, username, role, active FROM users ORDER BY username").fetchall()


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> sqlite3.Row | None:
    user = get_user(conn, username)
    ok = auth.verify_password(password, user["pw_hash"] if user else None)
    if not ok or not user["active"]:
        return None
    return user


def set_password(conn: sqlite3.Connection, username: str, password: str) -> bool:
    """Set a new password; it also signs the user out everywhere (session_epoch)."""
    pw_hash = auth.hash_password(auth.validate_password(password))
    cur = conn.execute(
        "UPDATE users SET pw_hash = ?, session_epoch = session_epoch + 1 WHERE username = ?",
        (pw_hash, username.strip().lower()),
    )
    return cur.rowcount > 0


def sign_out_everywhere(conn: sqlite3.Connection, user_id: int) -> None:
    """End every session of this account: sessions carry the epoch they were signed in at, and
    web/deps.py refuses any whose epoch no longer matches (Sign out, password changes, "sign out others")."""
    conn.execute("UPDATE users SET session_epoch = session_epoch + 1 WHERE id = ?", (user_id,))


def set_role(conn: sqlite3.Connection, username: str, role: str) -> bool:
    if role not in ROLES:
        raise ValueError(f"Role must be one of {ROLES}.")
    cur = conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username.strip().lower()))
    return cur.rowcount > 0


def set_active(conn: sqlite3.Connection, username: str, active: bool) -> bool:
    cur = conn.execute(
        "UPDATE users SET active = ? WHERE username = ?", (int(active), username.strip().lower())
    )
    return cur.rowcount > 0


def forget_memories(db_path, user_id: int) -> int:
    """Delete everything Erynne remembers about a user (LangGraph's store). Returns the count."""
    from langgraph.store.sqlite import SqliteStore

    from mnemerynne.graph import memory_namespace

    store_conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    try:
        store = SqliteStore(store_conn)
        store.setup()
        items = store.search(memory_namespace(user_id), limit=10_000)
        for item in items:  # deleted without reading them: admins can wipe, not read
            store.delete(memory_namespace(user_id), item.key)
        return len(items)
    finally:
        store_conn.close()


def delete_user(conn: sqlite3.Connection, username: str) -> bool:
    """Delete a user. Their private collection, files and conversations cascade away; their
    memories, the conversations' LangGraph checkpoints and the traces kept per conversation
    live outside those cascades and are deleted here (a new user may get this id again)."""
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username.strip().lower(),)).fetchone()
    if row is None:
        return False
    user_id = row[0]
    db_file = conn.execute("PRAGMA database_list").fetchone()[2]
    if db_file:  # "" for an in-memory database
        forget_memories(db_file, user_id)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    threads = [r[0] for r in conn.execute("SELECT id FROM conversations WHERE user_id = ?", (user_id,))]
    with transaction(conn):
        for thread in threads:
            # conversations.id is the LangGraph thread_id; as SqliteSaver.delete_thread does.
            for table in ("checkpoints", "writes"):
                if table in tables:
                    conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (thread,))
            conn.execute("DELETE FROM graph_runs WHERE conversation_id = ?", (thread,))
        conn.execute("DELETE FROM agent_calls WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return True


def is_admin(user: sqlite3.Row, bootstrap_admins: set[str] = frozenset()) -> bool:
    return user["role"] == "admin" or user["username"] in bootstrap_admins
