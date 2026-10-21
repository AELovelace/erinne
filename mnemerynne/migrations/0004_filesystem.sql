-- mnemerynne:no-fk
-- File manager: folders, a recycle bin, any file type, folder-scoped search.
-- Rebuilds `blobs` (wider kind CHECK) and `files` (drops the one-copy-per-
-- collection rule, adds folders and trash), so it runs with foreign keys off;
-- db.migrate runs a foreign_key_check before committing.

CREATE TABLE folders (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    parent_id     INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    -- Set only on the folder that was trashed, not on its contents: those are
    -- hidden because an ancestor is in the bin.
    deleted_at    TEXT,
    deleted_by    INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX folders_name ON folders(collection_id, COALESCE(parent_id, 0), lower(name))
    WHERE deleted_at IS NULL;
CREATE INDEX folders_parent ON folders(collection_id, parent_id);

-- ------------------------------------------------------------------ blobs
CREATE TABLE blobs_new (
    sha256       TEXT PRIMARY KEY,
    -- 'other' is stored but never parsed or indexed.
    kind         TEXT NOT NULL CHECK (kind IN ('pdf', 'md', 'txt', 'other')),
    mime         TEXT,
    size         INTEGER NOT NULL,
    unit_count   INTEGER,
    rel_path     TEXT NOT NULL,
    ocr_rel_path TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO blobs_new(sha256, kind, mime, size, unit_count, rel_path, ocr_rel_path, created_at)
    SELECT sha256, kind, CASE kind WHEN 'pdf' THEN 'application/pdf' ELSE 'text/markdown' END,
           size, unit_count, rel_path, ocr_rel_path, created_at FROM blobs;
DROP TABLE blobs;
ALTER TABLE blobs_new RENAME TO blobs;

-- ------------------------------------------------------------------ files
-- The same content may now sit in several folders of one collection. Live
-- names are unique per folder (enforced in mnemerynne/fs.py, since existing data
-- may already repeat a name).
CREATE TABLE files_new (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    folder_id     INTEGER REFERENCES folders(id) ON DELETE SET NULL,  -- NULL = collection root
    blob_sha256   TEXT NOT NULL REFERENCES blobs(sha256),
    display_name  TEXT NOT NULL,
    uploaded_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status        TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'processing', 'ready', 'failed', 'stored')),
    progress      TEXT,
    error         TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at    TEXT,
    deleted_by    INTEGER REFERENCES users(id) ON DELETE SET NULL
);
INSERT INTO files_new(id, collection_id, blob_sha256, display_name, uploaded_by, status, progress, error,
                      notes, created_at, updated_at)
    SELECT id, collection_id, blob_sha256, display_name, uploaded_by, status, progress, error,
           notes, created_at, updated_at FROM files;
DROP TABLE files;
ALTER TABLE files_new RENAME TO files;
CREATE INDEX files_collection ON files(collection_id, folder_id);
CREATE INDEX files_blob ON files(blob_sha256);

-- ----------------------------------------------------------------- search
-- Search scope travels with each chunk (and its vector): the collection, or 0
-- while the file is in the bin, and the folder (0 = collection root).
ALTER TABLE chunks ADD COLUMN folder_id INTEGER NOT NULL DEFAULT 0;

-- The "search in" choice a question was asked with (JSON), if any.
ALTER TABLE messages ADD COLUMN scope TEXT;
