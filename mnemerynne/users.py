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
                "INSERT INTO users(username, pw_hash, role) VALUES (?, ?, ?)", (name, pw_hash, role)
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
    pw_hash = auth.hash_password(auth.validate_password(password))
    cur = conn.execute(
        "UPDATE users SET pw_hash = ? WHERE username = ?", (pw_hash, username.strip().lower())
    )
    return cur.rowcount > 0


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


def delete_user(conn: sqlite3.Connection, username: str) -> bool:
    """Delete a user; their private collection and its files cascade away."""
    cur = conn.execute("DELETE FROM users WHERE username = ?", (username.strip().lower(),))
    return cur.rowcount > 0


def is_admin(user: sqlite3.Row, bootstrap_admins: set[str] = frozenset()) -> bool:
    return user["role"] == "admin" or user["username"] in bootstrap_admins
