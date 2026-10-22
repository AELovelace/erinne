-- SharePoint document libraries indexed into Mnemerynne, read-only
-- (mnemerynne/sharepoint.py). Each one is a library (collections.source =
-- 'sharepoint') kept in step with its drive by Graph delta, with the
-- tenant's app-only token. Who may see what follows SharePoint itself: the
-- library is open to people with a linked account in that tenant, and every
-- file is checked with that person's own token (cached in sharepoint_access).

ALTER TABLE collections ADD COLUMN source TEXT CHECK (source IN ('sharepoint'));
ALTER TABLE files ADD COLUMN remote_id TEXT;        -- the driveItem id
ALTER TABLE files ADD COLUMN remote_etag TEXT;      -- its cTag: changes when the content does
ALTER TABLE files ADD COLUMN web_url TEXT;          -- "Open in SharePoint"
ALTER TABLE folders ADD COLUMN remote_id TEXT;
CREATE INDEX files_remote ON files(collection_id, remote_id) WHERE remote_id IS NOT NULL;
CREATE INDEX folders_remote ON folders(collection_id, remote_id) WHERE remote_id IS NOT NULL;

CREATE TABLE sharepoint_drives (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL UNIQUE REFERENCES collections(id) ON DELETE CASCADE,
    tenant_row    INTEGER NOT NULL REFERENCES m365_tenants(id) ON DELETE CASCADE,
    site_id       TEXT NOT NULL,
    site_url      TEXT NOT NULL,
    drive_id      TEXT NOT NULL,
    web_url       TEXT NOT NULL DEFAULT '',
    delta_link    TEXT,
    last_sync_at  TEXT,
    status        TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'syncing', 'ok', 'error')),
    sync_started_at TEXT,                         -- a sync claims the library (worker or "Sync now")
    error         TEXT,
    files         INTEGER NOT NULL DEFAULT 0,
    UNIQUE (tenant_row, drive_id)
);

-- Whether a person's own Microsoft account may open a SharePoint item.
CREATE TABLE sharepoint_access (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_key   TEXT NOT NULL,                    -- <drive id>:<item id>
    ok         INTEGER NOT NULL CHECK (ok IN (0, 1)),
    checked_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, item_key)
);
