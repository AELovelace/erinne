"""Linking Mnemerynne users to on-premises domain accounts.

Users still sign in with their Mnemerynne username and password; an admin
links each one to their domain account. Admin rights then come from
membership of an AD group, chosen on the Settings tab.
"""

from __future__ import annotations

import sqlite3

from mnemerynne import users
from mnemerynne.directory import Directory

ADMIN_GROUP_KEY = "admin_group"
DEFAULT_ADMIN_GROUP = "Domain Admins"


class LinkError(ValueError):
    pass


# ---------------------------------------------------------------- admin group
def admin_group(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (ADMIN_GROUP_KEY,)).fetchone()
    return row["value"] if row else DEFAULT_ADMIN_GROUP


def set_admin_group(conn: sqlite3.Connection, name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > 256:
        raise ValueError("Pick a group.")
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (ADMIN_GROUP_KEY, name))
    return name


def is_admin(conn: sqlite3.Connection, user: sqlite3.Row, directory: Directory,
             bootstrap: set[str] = frozenset()) -> bool:
    """Admin role, bootstrap usernames, and users linked to a member of the admin group."""
    if users.is_admin(user, bootstrap):
        return True
    account = user["ad_account"]
    return bool(account) and admin_group(conn).lower() in directory.groups_of(account)


# ------------------------------------------------------------ domain accounts
def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Unlinked users first (they still need an admin), then by name."""
    return conn.execute("SELECT id, username, role, active, ad_account, created_at FROM users "
                        "ORDER BY ad_account IS NOT NULL, username COLLATE NOCASE").fetchall()


def link(conn: sqlite3.Connection, user_id: int, ad_account: str, directory: Directory) -> str:
    """Link a user to a domain account; returns the account name as stored."""
    if users.get_user_by_id(conn, user_id) is None:
        raise LinkError("No such user.")
    ad_account = (ad_account or "").strip().rsplit("\\", 1)[-1]
    if not ad_account:
        raise LinkError("Enter a domain account.")
    if not directory.account_exists(ad_account):
        raise LinkError(f"There's no domain account '{ad_account}'. Create it in Active Directory first.")
    taken = conn.execute("SELECT username FROM users WHERE ad_account = ? AND id != ?",
                         (ad_account, user_id)).fetchone()
    if taken:
        raise LinkError(f"'{ad_account}' is already linked to {taken['username']}.")
    conn.execute("UPDATE users SET ad_account = ? WHERE id = ?", (ad_account, user_id))
    return ad_account


def unlink(conn: sqlite3.Connection, user_id: int) -> bool:
    return conn.execute("UPDATE users SET ad_account = NULL WHERE id = ? AND ad_account IS NOT NULL",
                        (user_id,)).rowcount > 0
