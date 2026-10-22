"""Linking Mnemerynne users to on-premises domain accounts.

Users still sign in with their Mnemerynne username and password; an admin
links each one to their domain account. Admin rights then come from
membership of an AD group, chosen on the Settings tab.
"""

from __future__ import annotations

import sqlite3

from mnemerynne import share, users
from mnemerynne.directory import Directory

ADMIN_GROUP_KEY = "admin_group"
DEFAULT_ADMIN_GROUP = "Domain Admins"
AGENT_GROUP_KEY = "agent_group"


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
    return admin_via(conn, user, directory, bootstrap) is not None


def admin_via(conn: sqlite3.Connection, user: sqlite3.Row, directory: Directory,
              bootstrap: set[str] = frozenset()) -> str | None:
    """Why someone is an admin: 'role' (local admin), 'bootstrap', 'domain' (the admin group), or None."""
    if user["role"] == "admin":
        return "role"
    if user["username"] in bootstrap:
        return "bootstrap"
    account = user["ad_account"]
    return "domain" if account and admin_group(conn).lower() in directory.groups_of(account) else None


# ---------------------------------------------------------------- agent group
def agent_group(conn: sqlite3.Connection) -> str:
    """The AD group whose members get Erynne with tools (mnemerynne/agent.py); '' = nobody."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (AGENT_GROUP_KEY,)).fetchone()
    return row["value"] if row else ""


def set_agent_group(conn: sqlite3.Connection, name: str) -> str:
    """Pick the group ('' turns the agent off for everyone)."""
    name = (name or "").strip()
    if len(name) > 256:
        raise ValueError("Pick a group.")
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (AGENT_GROUP_KEY, name))
    return name


def is_agent_user(conn: sqlite3.Connection, user: sqlite3.Row, directory: Directory) -> bool:
    """Only people linked to a member of the agent group; admins aren't included
    by default (they join the group like anyone else)."""
    group, account = agent_group(conn), user["ad_account"]
    return bool(group) and bool(account) and group.lower() in directory.groups_of(account)


# ------------------------------------------------------------ domain accounts
def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Unlinked users first (they still need an admin), then by name."""
    return conn.execute("SELECT id, username, role, active, ad_account, created_at FROM users "
                        "ORDER BY ad_account IS NOT NULL, username COLLATE NOCASE").fetchall()


def check_link(conn: sqlite3.Connection, ad_account: str, directory: Directory, user_id: int | None = None) -> str:
    """The account name as it would be stored, or LinkError if it doesn't exist or is someone else's."""
    ad_account = (ad_account or "").strip().rsplit("\\", 1)[-1]
    if not ad_account:
        raise LinkError("Enter a domain account.")
    if not directory.account_exists(ad_account):
        raise LinkError(f"There's no domain account '{ad_account}'. Create it in Active Directory first.")
    taken = conn.execute("SELECT username FROM users WHERE ad_account = ? AND id != ?",
                         (ad_account, user_id or 0)).fetchone()
    if taken:
        raise LinkError(f"'{ad_account}' is already linked to {taken['username']}.")
    return ad_account


def link(conn: sqlite3.Connection, user_id: int, ad_account: str, directory: Directory) -> str:
    """Link a user to a domain account; returns the account name as stored."""
    if users.get_user_by_id(conn, user_id) is None:
        raise LinkError("No such user.")
    ad_account = check_link(conn, ad_account, directory, user_id)
    conn.execute("UPDATE users SET ad_account = ? WHERE id = ?", (ad_account, user_id))
    _secure_inbox(conn, user_id, ad_account)
    return ad_account


def _secure_inbox(conn: sqlite3.Connection, user_id: int, ad_account: str | None) -> None:
    """Only the (newly) linked domain account may open the inbox folder on the share."""
    row = conn.execute("SELECT share_path FROM collections WHERE kind = 'private' AND owner_user_id = ?",
                       (user_id,)).fetchone()
    rt = share.current()
    if row and row["share_path"] and rt is not None:
        share.secure_inbox(rt.root / row["share_path"], ad_account)


def unlink(conn: sqlite3.Connection, user_id: int) -> bool:
    changed = conn.execute("UPDATE users SET ad_account = NULL WHERE id = ? AND ad_account IS NOT NULL",
                           (user_id,)).rowcount > 0
    if changed:
        _secure_inbox(conn, user_id, None)
    return changed
