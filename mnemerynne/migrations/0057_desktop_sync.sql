-- mnemerynne:no-fk
-- Desktop backup (mnemerynne/sync.py, docs/plans/desktop-sync-client.md): an app on a person's computer copies
-- folders into Users\<name>\Backups\<computer>\<label> through /api/sync/*. Who may use it is meta
-- 'backups_settings' (on/off and a group).

-- What a token is for: 'mcp' (Claude and other MCP clients, as before) or 'sync' (the backup app, which may only
-- touch the person's Backups folder). Each kind is refused where the other belongs.
ALTER TABLE api_tokens ADD COLUMN scope TEXT NOT NULL DEFAULT 'mcp' CHECK (scope IN ('mcp', 'sync'));
ALTER TABLE oauth_grants ADD COLUMN scope TEXT NOT NULL DEFAULT 'mcp' CHECK (scope IN ('mcp', 'sync'));

-- Files under a folder with no_index = 1 are kept, listed and previewed but never read for search. NULL: as the
-- folder above it; the nearest folder with a value decides (Backups is 1, a backed-up folder made Searchable is 0).
ALTER TABLE folders ADD COLUMN no_index INTEGER CHECK (no_index IN (0, 1));

-- A folder on one computer that is backed up: Backups\<computer>\<label> in the owner's File box.
CREATE TABLE sync_roots (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    computer     TEXT NOT NULL,
    label        TEXT NOT NULL,
    folder_id    INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT
);
CREATE UNIQUE INDEX sync_roots_name ON sync_roots(user_id, lower(computer), lower(label));

-- A big file arriving in parts (resumable uploads), until it is finished or swept.
CREATE TABLE sync_uploads (
    id             TEXT PRIMARY KEY,           -- random, given to the app
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    root_id        INTEGER NOT NULL REFERENCES sync_roots(id) ON DELETE CASCADE,
    path           TEXT NOT NULL,
    size           INTEGER NOT NULL,
    sha256         TEXT NOT NULL,
    parts_received TEXT NOT NULL DEFAULT '[]', -- JSON list of part numbers
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Overwrites from the backup app keep the old content as a version too: via 'sync'.
CREATE TABLE file_versions_new (
    id         INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    sha256     TEXT NOT NULL,
    size       INTEGER NOT NULL,
    rel_path   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    via        TEXT NOT NULL CHECK (via IN ('claude', 'restore', 'erynne', 'web', 'sync')),
    token_id   INTEGER REFERENCES api_tokens(id) ON DELETE SET NULL,
    summary    TEXT NOT NULL DEFAULT ''
);
INSERT INTO file_versions_new SELECT id, file_id, sha256, size, rel_path, created_at, created_by, via, token_id, summary
FROM file_versions;
DROP TABLE file_versions;
ALTER TABLE file_versions_new RENAME TO file_versions;
CREATE INDEX file_versions_file ON file_versions(file_id, id);
