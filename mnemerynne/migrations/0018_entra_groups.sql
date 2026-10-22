-- Entra (Microsoft 365) groups alongside AD groups (mnemerynne/group_refs.py).
-- A group setting (admin, agent, an API's group) holds one or more references
-- separated by ';': an AD group name, or an Entra group's key
-- 'entra:<tenant id>:<group id>'. The worker reads each linked account's
-- Entra groups every few minutes with the tenant's app-only token; entries
-- older than 30 minutes count as no membership, like a stale AD snapshot.

-- Every group in each tenant, for pickers and names ("Gasco / Front Desk").
CREATE TABLE entra_groups (
    group_key  TEXT PRIMARY KEY,                    -- entra:<tenant id>:<group id>, lower-case
    tenant_row INTEGER NOT NULL REFERENCES m365_tenants(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    seen_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX entra_groups_tenant ON entra_groups(tenant_row);

-- Which Entra groups each linked account is in (nested ones included).
CREATE TABLE entra_memberships (
    identity_id  INTEGER NOT NULL REFERENCES m365_identities(id) ON DELETE CASCADE,
    group_key    TEXT NOT NULL,
    refreshed_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (identity_id, group_key)
);
CREATE INDEX entra_memberships_key ON entra_memberships(group_key);

-- A Mnemerynne group can also take in everyone in one Entra group.
ALTER TABLE groups ADD COLUMN entra_key TEXT;
