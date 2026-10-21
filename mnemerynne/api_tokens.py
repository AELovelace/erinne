"""Personal API tokens: how Claude Desktop (and other MCP clients) act as a user.

A token is `mnemerynne_` + 43 random URL-safe characters. Only its SHA-256 is
stored. A token acts with its user's permissions at the moment of each call,
so deactivating the user or removing a grant takes effect immediately.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass

PREFIX = "mnemerynne_"
# Tokens made before the rename keep working until they expire.
LEGACY_PREFIXES = ("erinne_", "erynne_")
EXPIRY_DAYS = (30, 90, 365)


@dataclass(frozen=True)
class Caller:
    user_id: int
    username: str
    token_id: int


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(conn: sqlite3.Connection, user_id: int, name: str, days: int | None = 90) -> tuple[int, str]:
    """Make a token; returns (id, token). The token can't be recovered later."""
    name = (name or "").strip()[:80] or "Claude Desktop"
    token = PREFIX + secrets.token_urlsafe(32)
    expires = f"+{int(days)} days" if days else None
    cur = conn.execute(
        "INSERT INTO api_tokens(user_id, name, token_hash, prefix, expires_at) "
        "VALUES (?, ?, ?, ?, CASE WHEN ? IS NULL THEN NULL ELSE datetime('now', ?) END)",
        (user_id, name, _hash(token), token[: len(PREFIX) + 4], expires, expires))
    return cur.lastrowid, token


def verify(conn: sqlite3.Connection, token: str | None, *, touch: bool = True) -> Caller | None:
    """The caller behind a token, or None if it is unknown, revoked, expired,
    or belongs to a deactivated user."""
    if not token or not token.startswith((PREFIX, *LEGACY_PREFIXES)):
        return None
    row = conn.execute(
        "SELECT t.id, t.user_id, u.username FROM api_tokens t JOIN users u ON u.id = t.user_id "
        "WHERE t.token_hash = ? AND t.revoked_at IS NULL AND u.active = 1 "
        "AND (t.expires_at IS NULL OR t.expires_at > datetime('now'))", (_hash(token),)).fetchone()
    if row is None:
        return None
    if touch:
        conn.execute("UPDATE api_tokens SET last_used_at = datetime('now') WHERE id = ?", (row["id"],))
    return Caller(row["user_id"], row["username"], row["id"])


def list_for_user(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, prefix, created_at, expires_at, last_used_at, revoked_at, "
        "(revoked_at IS NULL AND (expires_at IS NULL OR expires_at > datetime('now'))) AS active "
        "FROM api_tokens WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
    return [{**dict(r), "active": bool(r["active"])} for r in rows]


def revoke(conn: sqlite3.Connection, token_id: int, user_id: int | None = None) -> bool:
    """Revoke one token (only the owner's when `user_id` is given)."""
    sql = "UPDATE api_tokens SET revoked_at = datetime('now') WHERE id = ? AND revoked_at IS NULL"
    params: tuple = (token_id,)
    if user_id is not None:
        sql += " AND user_id = ?"
        params += (user_id,)
    return conn.execute(sql, params).rowcount > 0


def revoke_all(conn: sqlite3.Connection, user_id: int) -> int:
    return conn.execute("UPDATE api_tokens SET revoked_at = datetime('now') "
                        "WHERE user_id = ? AND revoked_at IS NULL", (user_id,)).rowcount
