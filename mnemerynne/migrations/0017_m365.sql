-- Microsoft 365 (mnemerynne/m365.py): the tenants Mnemerynne works with, and
-- the Microsoft accounts people link to their Mnemerynne user on the Connect
-- page. Sign-in itself is unchanged: people use their Mnemerynne account; a
-- linked account only gives Mnemerynne delegated access (SharePoint, mail).

CREATE TABLE m365_tenants (
    id                INTEGER PRIMARY KEY,
    tenant_id         TEXT NOT NULL UNIQUE COLLATE NOCASE,   -- the directory's GUID
    label             TEXT NOT NULL,                         -- e.g. "Eternal Hotels", "Gasco"
    domain            TEXT NOT NULL DEFAULT '',              -- the domain it was added by, for display
    -- 'ad': its users are synced from our AD; a linked account must be the
    --       Mnemerynne user's own domain account. 'cloud': separate staff.
    mode              TEXT NOT NULL DEFAULT 'cloud' CHECK (mode IN ('ad', 'cloud')),
    client_id         TEXT NOT NULL DEFAULT '',              -- its own app registration; '' = the shared one
    client_secret_enc BLOB,
    enabled           INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    consent_ok_at     TEXT,                                  -- last app-only token fetch that worked
    consent_error     TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE m365_identities (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_row   INTEGER NOT NULL REFERENCES m365_tenants(id) ON DELETE CASCADE,
    oid          TEXT NOT NULL COLLATE NOCASE,               -- the account's object id in that tenant
    upn          TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    refresh_enc  BLOB,                                       -- encrypted; NULL once Microsoft refused it
    scopes       TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'relink')),
    linked_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,
    UNIQUE (tenant_row, oid)
);
CREATE INDEX m365_identities_user ON m365_identities(user_id);
