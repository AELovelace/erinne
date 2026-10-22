-- Claude editing files (mnemerynne/editing.py, the MCP edit tools).
-- Claude may change a file only where all of these hold: the person can edit
-- there, their token allows editing, the collection is shared with Claude, and
-- either the collection allows Claude to edit (claude_edit, off by default) or
-- the file is in the person's own Agents folder (folders.role = 'agents'),
-- which every inbox has and where editing is on by default.

ALTER TABLE collections ADD COLUMN claude_edit INTEGER NOT NULL DEFAULT 0 CHECK (claude_edit IN (0, 1));

ALTER TABLE folders ADD COLUMN role TEXT CHECK (role IN ('agents'));
CREATE UNIQUE INDEX folders_one_agents ON folders(collection_id) WHERE role = 'agents' AND deleted_at IS NULL;

-- Tokens made before this stay search-only until their owner allows editing.
ALTER TABLE api_tokens ADD COLUMN can_edit INTEGER NOT NULL DEFAULT 0 CHECK (can_edit IN (0, 1));

-- The content a file had before each change made through Claude (or a
-- restore), so it can be put back. The bytes live under <data_dir>/versions.
CREATE TABLE file_versions (
    id         INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    sha256     TEXT NOT NULL,
    size       INTEGER NOT NULL,
    rel_path   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    via        TEXT NOT NULL CHECK (via IN ('claude', 'restore')),
    token_id   INTEGER REFERENCES api_tokens(id) ON DELETE SET NULL,
    summary    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX file_versions_file ON file_versions(file_id, id);

-- Who last changed a file through Claude, shown in Files ("edited via Claude for alice").
ALTER TABLE files ADD COLUMN edited_via TEXT;
