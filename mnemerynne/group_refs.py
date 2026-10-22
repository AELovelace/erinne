"""Group references: AD groups and Entra (Microsoft 365) groups in one place.

A group setting (the admin group, the agent group, an API's group, a shared
mailbox's group) holds one or more references separated by ';':
  * an AD group name, as before ("erynne-agent"; AD names can't hold ';'), or
  * an Entra group key, "entra:<tenant id>:<group id>". People type or pick
    it as "Tenant / Group" ("Gasco / Front Desk"); it is stored by id, so
    renaming the group in Entra doesn't break anything.

Someone is in a reference when their linked domain account is in that AD
group (directory.groups_of), or one of their linked Microsoft accounts was in
that Entra group at the last refresh (worker, m365.refresh_groups), at most
FRESH_MINUTES ago. Older memberships count as none: it fails closed.
"""

from __future__ import annotations

import sqlite3

FRESH_MINUTES = 30
PREFIX = "entra:"


class GroupRefError(ValueError):
    pass


def split(value: str | None) -> list[str]:
    return [r.strip() for r in (value or "").split(";") if r.strip()]


def join(refs: list[str]) -> str:
    return "; ".join(dict.fromkeys(refs))


def key(tenant_id: str, group_id: str) -> str:
    return f"{PREFIX}{tenant_id.lower()}:{group_id.lower()}"


def member_of(conn: sqlite3.Connection, user, directory) -> set[str]:
    """Every group reference the user is in: AD names (lower-case) and Entra keys."""
    account = user["ad_account"] if "ad_account" in user.keys() else None
    out = set(directory.groups_of(account)) if account and directory is not None else set()
    out |= {r[0] for r in conn.execute(
        "SELECT m.group_key FROM entra_memberships m JOIN m365_identities i ON i.id = m.identity_id "
        "JOIN m365_tenants t ON t.id = i.tenant_row WHERE i.user_id = ? AND t.enabled = 1 "
        "AND m.refreshed_at > datetime('now', ?)", (user["id"], f"-{FRESH_MINUTES} minutes"))}
    return out


def in_any(conn: sqlite3.Connection, user, directory, value: str | None, groups: set[str] | None = None) -> bool:
    """Whether the user is in any group the setting names (`groups`: member_of, if already known)."""
    refs = split(value)
    if not refs:
        return False
    groups = member_of(conn, user, directory) if groups is None else groups
    return any(r.lower() in groups for r in refs)


# ------------------------------------------------------------ names, pickers
def _entra_label(conn: sqlite3.Connection, ref: str) -> str:
    row = conn.execute("SELECT g.name, t.label FROM entra_groups g JOIN m365_tenants t ON t.id = g.tenant_row "
                       "WHERE g.group_key = ?", (ref.lower(),)).fetchone()
    return f"{row['label']} / {row['name']}" if row else ref


def label(conn: sqlite3.Connection, value: str | None) -> str:
    """The setting as people read it: AD names as they are, Entra groups as "Tenant / Group"."""
    return join([_entra_label(conn, r) if r.lower().startswith(PREFIX) else r for r in split(value)])


def choices(conn: sqlite3.Connection, ad_groups: list[str]) -> list[str]:
    """What the group boxes suggest: AD groups, then each tenant's Entra groups."""
    entra = [f"{r['label']} / {r['name']}" for r in conn.execute(
        "SELECT g.name, t.label FROM entra_groups g JOIN m365_tenants t ON t.id = g.tenant_row "
        "WHERE t.enabled = 1 ORDER BY t.label COLLATE NOCASE, g.name COLLATE NOCASE")]
    return list(ad_groups) + entra


def resolve(conn: sqlite3.Connection, text: str | None, ad_groups: list[str]) -> str:
    """Typed references ("erynne-agent; Gasco / Front Desk") as stored ones, or
    GroupRefError naming the first that doesn't exist. `ad_groups`: the
    domain's groups ([] when there is no domain: AD names are then kept as typed)."""
    known_ad = {g.lower(): g for g in ad_groups}
    out = []
    for ref in split(text):
        if ref.lower().startswith(PREFIX):
            if conn.execute("SELECT 1 FROM entra_groups WHERE group_key = ?", (ref.lower(),)).fetchone() is None:
                raise GroupRefError(f"There's no Entra group {ref}.")
            out.append(ref.lower())
            continue
        if " / " in ref:
            tenant_label, name = (p.strip() for p in ref.split(" / ", 1))
            rows = conn.execute(
                "SELECT g.group_key FROM entra_groups g JOIN m365_tenants t ON t.id = g.tenant_row "
                "WHERE t.label = ? COLLATE NOCASE AND g.name = ? COLLATE NOCASE", (tenant_label, name)).fetchall()
            if len(rows) == 1:
                out.append(rows[0][0])
                continue
            if len(rows) > 1:
                raise GroupRefError(f"{tenant_label} has several groups called “{name}”; rename one in Entra.")
            if conn.execute("SELECT 1 FROM m365_tenants WHERE label = ? COLLATE NOCASE", (tenant_label,)).fetchone():
                raise GroupRefError(f"{tenant_label} has no group called “{name}” (the list refreshes every few "
                                    f"minutes).")
        name = ref.rsplit("\\", 1)[-1]
        if known_ad and name.lower() not in known_ad:
            raise GroupRefError(f"There's no domain group “{name}”.")
        out.append(known_ad.get(name.lower(), name))
    return join(out)
