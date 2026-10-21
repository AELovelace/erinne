-- mnemerynne schema v1. Applied inside a transaction by mnemerynne.db.migrate().
-- chunk_vec (sqlite-vec) is created by db.ensure_vector_table() because its
-- vector dimension comes from configuration.

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ---------------------------------------------------------------- people
CREATE TABLE users (
    id         INTEGER PRIMARY KEY,
    username   TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash    TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    active     INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE groups (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE group_members (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, user_id)
);

-- ----------------------------------------------------------- collections
-- Every user owns exactly one private collection (their inbox). Libraries are
-- shared collections published by admins and granted to users or groups.
CREATE TABLE collections (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('private', 'library')),
    name          TEXT NOT NULL,
    owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((kind = 'private') = (owner_user_id IS NOT NULL))
);
CREATE UNIQUE INDEX collections_one_private_per_user
    ON collections(owner_user_id) WHERE kind = 'private';
CREATE UNIQUE INDEX collections_library_name
    ON collections(name COLLATE NOCASE) WHERE kind = 'library';

CREATE TABLE collection_grants (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    user_id       INTEGER REFERENCES users(id) ON DELETE CASCADE,
    group_id      INTEGER REFERENCES groups(id) ON DELETE CASCADE,
    can_write     INTEGER NOT NULL DEFAULT 0 CHECK (can_write IN (0, 1)),
    CHECK ((user_id IS NULL) != (group_id IS NULL))
);
CREATE UNIQUE INDEX grants_user  ON collection_grants(collection_id, user_id)  WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX grants_group ON collection_grants(collection_id, group_id) WHERE group_id IS NOT NULL;

-- ------------------------------------------------------------- documents
-- Blobs are content-addressed (sha256) and stored under data/blobs/ by
-- relative path only, so the data directory can move between machines.
CREATE TABLE blobs (
    sha256       TEXT PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('pdf', 'md')),
    size         INTEGER NOT NULL,
    unit_count   INTEGER,
    rel_path     TEXT NOT NULL,
    ocr_rel_path TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Extracted text per unit: a PDF page, or a Markdown heading section.
CREATE TABLE blob_units (
    sha256       TEXT NOT NULL REFERENCES blobs(sha256) ON DELETE CASCADE,
    unit_no      INTEGER NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('page', 'section')),
    heading_path TEXT NOT NULL DEFAULT '',
    anchor       TEXT,
    text         TEXT NOT NULL,
    ocr          INTEGER NOT NULL DEFAULT 0 CHECK (ocr IN (0, 1)),
    PRIMARY KEY (sha256, unit_no)
);

CREATE TABLE files (
    id            INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    blob_sha256   TEXT NOT NULL REFERENCES blobs(sha256),
    display_name  TEXT NOT NULL,
    uploaded_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status        TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'processing', 'ready', 'failed')),
    progress      TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (collection_id, blob_sha256)
);
CREATE INDEX files_collection ON files(collection_id);

CREATE TABLE chunks (
    id            INTEGER PRIMARY KEY,
    file_id       INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    collection_id INTEGER NOT NULL,
    ord           INTEGER NOT NULL,
    page_start    INTEGER,
    page_end      INTEGER,
    anchor        TEXT,
    section_path  TEXT NOT NULL DEFAULT '',
    text          TEXT NOT NULL,
    tokens        INTEGER
);
CREATE INDEX chunks_file ON chunks(file_id);
CREATE INDEX chunks_collection ON chunks(collection_id);

-- Keyword index over chunks (external content; kept in sync by triggers).
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    section_path, text,
    content = 'chunks', content_rowid = 'id',
    tokenize = 'porter unicode61'
);
CREATE TRIGGER chunks_fts_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, section_path, text) VALUES (new.id, new.section_path, new.text);
END;
CREATE TRIGGER chunks_fts_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, section_path, text)
        VALUES ('delete', old.id, old.section_path, old.text);
END;
CREATE TRIGGER chunks_fts_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, section_path, text)
        VALUES ('delete', old.id, old.section_path, old.text);
    INSERT INTO chunks_fts(rowid, section_path, text) VALUES (new.id, new.section_path, new.text);
END;

-- ----------------------------------------------------------- job queue
CREATE TABLE jobs (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,
    file_id    INTEGER REFERENCES files(id) ON DELETE CASCADE,
    status     TEXT NOT NULL DEFAULT 'queued'
               CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    run_after  TEXT NOT NULL DEFAULT (datetime('now')),
    locked_at  TEXT,
    error      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX jobs_ready ON jobs(status, run_after);

-- -------------------------------------------------------- conversations
-- conversations.id doubles as the LangGraph thread_id.
CREATE TABLE conversations (
    id         TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX conversations_user ON conversations(user_id, updated_at);

CREATE TABLE messages (
    id              INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    status          TEXT CHECK (status IN ('answered', 'not_found', 'error')),
    model           TEXT,
    prompt_version  TEXT,
    latency_ms      INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX messages_conversation ON messages(conversation_id, id);

CREATE TABLE message_sources (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    n          INTEGER NOT NULL,
    chunk_id   INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
    file_id    INTEGER REFERENCES files(id) ON DELETE SET NULL,
    page_start INTEGER,
    anchor     TEXT,
    cited      INTEGER NOT NULL DEFAULT 0 CHECK (cited IN (0, 1)),
    PRIMARY KEY (message_id, n)
);

CREATE TABLE feedback (
    id         INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    rating     INTEGER NOT NULL CHECK (rating IN (-1, 1)),
    comment    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Local trace log (replaces LangSmith, which must stay off).
CREATE TABLE graph_runs (
    id              INTEGER PRIMARY KEY,
    conversation_id TEXT,
    node            TEXT NOT NULL,
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    duration_ms     INTEGER,
    detail          TEXT
);
