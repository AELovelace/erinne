-- sync.db: the desktop backup app's tables, moved out of the core database (docs/plans/services-rewrite.md, Phase 10):
-- the folders each computer backs up (sync_roots) and the big uploads arriving in parts (sync_uploads, one write per
-- part, no longer in line for core's lock). The same tables as core's migrations left them, without their references
-- into core: db.FEATURE_DBS['sync'] lists those columns (a person's roots and uploads go with them; a root whose
-- folder goes keeps its row without one, and asks to be registered again).

CREATE TABLE sync_roots (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL,
    computer     TEXT NOT NULL,
    label        TEXT NOT NULL,
    folder_id    INTEGER,                        -- core's folder; NULL once it's gone (folder_null_columns)
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT
);
CREATE UNIQUE INDEX sync_roots_name ON sync_roots(user_id, lower(computer), lower(label));

CREATE TABLE sync_uploads (
    id             TEXT PRIMARY KEY,           -- random, given to the app
    user_id        INTEGER NOT NULL,
    root_id        INTEGER NOT NULL REFERENCES sync_roots(id) ON DELETE CASCADE,
    path           TEXT NOT NULL,
    size           INTEGER NOT NULL,
    sha256         TEXT NOT NULL,
    parts_received TEXT NOT NULL DEFAULT '[]', -- JSON list of part numbers
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
