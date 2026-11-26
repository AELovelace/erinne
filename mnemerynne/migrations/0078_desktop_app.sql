-- mnemerynne:no-fk
-- The desktop app (clients/desktop-app, mnemerynne/desktop.py, mnemerynne/drive.py): a Windows app that shows the site
-- in tabs and puts picked libraries in File Explorer as a drive (files on demand). It signs in over OAuth with the
-- scope 'desktop', which only works on /api/desktop/* and /api/drive/*. Who may use it is meta 'desktop_app_group'.

-- 'desktop' joins the OAuth scopes. SQLite can't change a CHECK in place, so both tables are rebuilt (same columns,
-- same order, same index).
CREATE TABLE oauth_codes_new (
    code_hash      TEXT PRIMARY KEY,
    client_row     INTEGER NOT NULL REFERENCES oauth_clients(id) ON DELETE CASCADE,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    redirect_uri   TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    can_edit       INTEGER NOT NULL CHECK (can_edit IN (0, 1)),
    expires_at     TEXT NOT NULL,
    used_at        TEXT,
    grant_id       INTEGER,
    scope          TEXT NOT NULL DEFAULT 'mcp' CHECK (scope IN ('mcp', 'sync', 'desktop'))
);
INSERT INTO oauth_codes_new SELECT code_hash, client_row, user_id, redirect_uri, code_challenge, can_edit, expires_at,
    used_at, grant_id, scope FROM oauth_codes;
DROP TABLE oauth_codes;
ALTER TABLE oauth_codes_new RENAME TO oauth_codes;

CREATE TABLE oauth_grants_new (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_row   INTEGER NOT NULL REFERENCES oauth_clients(id) ON DELETE CASCADE,
    can_edit     INTEGER NOT NULL DEFAULT 0 CHECK (can_edit IN (0, 1)),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,
    revoked_at   TEXT,
    scope        TEXT NOT NULL DEFAULT 'mcp' CHECK (scope IN ('mcp', 'sync', 'desktop'))
);
INSERT INTO oauth_grants_new SELECT id, user_id, client_row, can_edit, created_at, last_used_at, revoked_at, scope
FROM oauth_grants;
DROP TABLE oauth_grants;
ALTER TABLE oauth_grants_new RENAME TO oauth_grants;
CREATE INDEX oauth_grants_user ON oauth_grants(user_id);

-- Saving a file from the drive keeps the old content as a version: via 'drive'.
CREATE TABLE file_versions_new (
    id         INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    sha256     TEXT NOT NULL,
    size       INTEGER NOT NULL,
    rel_path   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    via        TEXT NOT NULL CHECK (via IN ('claude', 'restore', 'erynne', 'web', 'sync', 'drive')),
    token_id   INTEGER REFERENCES api_tokens(id) ON DELETE SET NULL,
    summary    TEXT NOT NULL DEFAULT ''
);
INSERT INTO file_versions_new SELECT id, file_id, sha256, size, rel_path, created_at, created_by, via, token_id, summary
FROM file_versions;
DROP TABLE file_versions;
ALTER TABLE file_versions_new RENAME TO file_versions;
CREATE INDEX file_versions_file ON file_versions(file_id, id);

-- The drive's change feed: every add, rename, move, new content, bin or restore of a file or folder takes the next
-- number from one counter, so "what changed after N" never misses anything (SQLite has one writer at a time, so the
-- numbers are given out in commit order). Triggers keep it, whatever code path made the change (web, share watcher,
-- Claude, backups). Rows from before this start at 0: the app lists folders the first time it opens them anyway.
CREATE TABLE drive_seq (
    id  INTEGER PRIMARY KEY CHECK (id = 1),
    seq INTEGER NOT NULL
);
INSERT INTO drive_seq(id, seq) VALUES (1, 0);
ALTER TABLE files ADD COLUMN change_seq INTEGER NOT NULL DEFAULT 0;
ALTER TABLE folders ADD COLUMN change_seq INTEGER NOT NULL DEFAULT 0;
CREATE INDEX files_change_seq ON files(collection_id, change_seq);
CREATE INDEX folders_change_seq ON folders(collection_id, change_seq);

CREATE TRIGGER files_drive_ai AFTER INSERT ON files BEGIN
    UPDATE drive_seq SET seq = seq + 1 WHERE id = 1;
    UPDATE files SET change_seq = (SELECT seq FROM drive_seq WHERE id = 1) WHERE id = NEW.id;
END;
CREATE TRIGGER files_drive_au AFTER UPDATE OF collection_id, folder_id, blob_sha256, display_name, deleted_at
    ON files BEGIN
    UPDATE drive_seq SET seq = seq + 1 WHERE id = 1;
    UPDATE files SET change_seq = (SELECT seq FROM drive_seq WHERE id = 1) WHERE id = NEW.id;
END;
CREATE TRIGGER folders_drive_ai AFTER INSERT ON folders BEGIN
    UPDATE drive_seq SET seq = seq + 1 WHERE id = 1;
    UPDATE folders SET change_seq = (SELECT seq FROM drive_seq WHERE id = 1) WHERE id = NEW.id;
END;
CREATE TRIGGER folders_drive_au AFTER UPDATE OF collection_id, parent_id, name, deleted_at ON folders BEGIN
    UPDATE drive_seq SET seq = seq + 1 WHERE id = 1;
    UPDATE folders SET change_seq = (SELECT seq FROM drive_seq WHERE id = 1) WHERE id = NEW.id;
END;

-- Single-use tickets that sign the app's tabs in to the web pages again without a password (desktop.py): the app
-- holds an OAuth token, asks for a ticket, and opens /desktop/signin?t=… in the tab. Only the hash is stored.
CREATE TABLE desktop_tickets (
    ticket_hash TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    grant_id    INTEGER REFERENCES oauth_grants(id) ON DELETE CASCADE,
    expires_at  TEXT NOT NULL,
    used_at     TEXT
);
