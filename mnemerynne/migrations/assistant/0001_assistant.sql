-- assistant.db: Ask's conversations, their answers and sources, the ratings, the graph's timings, and LangGraph's
-- checkpoints (conversation state) and store (Erynne's memories), moved out of the core database
-- (docs/plans/services-rewrite.md, Phase 7). The same tables as core's migrations and LangGraph left them, without
-- their references into core (users, files, chunks, api_calls): SQLite can't follow a reference into another file,
-- so db.FEATURE_DBS['assistant'] lists those columns for forget_user and sweep_orphans.
-- agent_proposals stays in core: applying one writes a file and marks the proposal in one transaction.

CREATE TABLE conversations (
    id         TEXT PRIMARY KEY,                 -- also LangGraph's thread_id
    user_id    INTEGER NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX conversations_user ON conversations(user_id, updated_at);

-- AUTOINCREMENT: core's agent_proposals.message_id points here across the files, so an id is never handed out
-- again after its message is deleted.
CREATE TABLE messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content          TEXT NOT NULL,
    status           TEXT CHECK (status IN ('answered', 'not_found', 'error')),
    model            TEXT,
    prompt_version   TEXT,
    latency_ms       INTEGER,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    route            TEXT,
    scope            TEXT,
    tokens_in        INTEGER,
    tokens_out       INTEGER,
    tokens_external  INTEGER,
    tokens_estimated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX messages_conversation ON messages(conversation_id, id);

CREATE TABLE message_sources (
    message_id   INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    n            INTEGER NOT NULL,
    chunk_id     INTEGER,                        -- search.db's chunk (gone: the source shows without its section)
    file_id      INTEGER,                        -- core's file (gone: the source shows without a link)
    page_start   INTEGER,
    anchor       TEXT,
    cited        INTEGER NOT NULL DEFAULT 0 CHECK (cited IN (0, 1)),
    api_call_id  INTEGER,                        -- core's api_calls row, for a live API answer
    ext_kind     TEXT,
    ext_title    TEXT,
    ext_location TEXT,
    ext_url      TEXT,
    PRIMARY KEY (message_id, n)
);

CREATE TABLE feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id    INTEGER,                          -- NULL once that person is deleted (user_null_columns)
    rating     INTEGER NOT NULL CHECK (rating IN (-1, 1)),
    comment    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE feedback_archive (
    id              INTEGER PRIMARY KEY,
    feedback_id     INTEGER,                 -- the feedback row it was taken from
    rated_at        TEXT NOT NULL,
    archived_at     TEXT NOT NULL DEFAULT (datetime('now')),
    message_id      INTEGER NOT NULL,
    conversation_id TEXT,
    user_id         INTEGER,
    username        TEXT,
    rating          INTEGER NOT NULL CHECK (rating IN (-1, 1)),
    comment         TEXT,
    question        TEXT,
    question_scope  TEXT,
    answer          TEXT NOT NULL,
    answered_at     TEXT,
    status          TEXT,
    route           TEXT,
    model           TEXT,
    prompt_version  TEXT,
    sources         TEXT NOT NULL DEFAULT '[]',   -- JSON: each numbered source with its text then
    tool_calls      TEXT NOT NULL DEFAULT '[]'    -- JSON: the agent's tool calls for this answer
);
CREATE INDEX feedback_archive_rated ON feedback_archive(rated_at);
CREATE INDEX feedback_archive_message ON feedback_archive(message_id, user_id, id);

CREATE TABLE graph_runs (
    id              INTEGER PRIMARY KEY,
    conversation_id TEXT,
    node            TEXT NOT NULL,
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    duration_ms     INTEGER,
    detail          TEXT
);

-- LangGraph's checkpointer (langgraph-checkpoint-sqlite's AsyncSqliteSaver.setup, as of 3.1): its CREATE TABLE IF
-- NOT EXISTS finds these and does nothing.
CREATE TABLE checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint BLOB,
    metadata BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE TABLE writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_path TEXT NOT NULL DEFAULT '',
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    value BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

-- LangGraph's store (SqliteStore: Erynne's memories), at its migration 4 with all five recorded as done, so its
-- setup() has nothing left to run. A later LangGraph's migration 5 on would still run here.
CREATE TABLE store_migrations (
    v INTEGER PRIMARY KEY
);
INSERT INTO store_migrations(v) VALUES (0), (1), (2), (3), (4);
CREATE TABLE store (
    prefix text NOT NULL,
    key text NOT NULL,
    value text NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    ttl_minutes REAL,
    PRIMARY KEY (prefix, key)
);
CREATE INDEX store_prefix_idx ON store (prefix);
CREATE INDEX idx_store_expires_at ON store (expires_at) WHERE expires_at IS NOT NULL;

-- A conversation that goes (deleted in Ask, its person deleted, or swept as an orphan) takes its LangGraph state
-- and timings with it, whichever code deleted it. In core this was users.delete_user's job.
CREATE TRIGGER conversations_gone AFTER DELETE ON conversations BEGIN
    DELETE FROM checkpoints WHERE thread_id = old.id;
    DELETE FROM writes WHERE thread_id = old.id;
    DELETE FROM graph_runs WHERE conversation_id = old.id;
END;
