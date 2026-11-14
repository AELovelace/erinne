-- logs.db: the busy call logs, moved out of the core database (docs/plans/services-rewrite.md, Phase 8): each tool
-- Claude called over MCP, each internal API call, each of Erynne's agent tool calls. Written on every call, read
-- by admins and kept only *_days (retention.py), so they get a write lock of their own. The same tables as core's
-- migrations left them, without their references into core (users, tokens, grants, APIs): SQLite can't follow a
-- reference into another file, so db.FEATURE_DBS['logs'] lists the users' columns for forget_user and
-- sweep_orphans. access_log (who changed whose access) stays in core, in the same transaction as the change.

CREATE TABLE mcp_calls (
    id       INTEGER PRIMARY KEY,
    at       TEXT NOT NULL DEFAULT (datetime('now')),
    user_id  INTEGER,                      -- NULL once that person is deleted
    token_id INTEGER,                      -- core's api_tokens row (a Desktop connection)
    tool     TEXT NOT NULL,
    args     TEXT NOT NULL,                -- JSON
    file_ids TEXT NOT NULL DEFAULT '[]',   -- JSON list of files whose text was returned
    ok       INTEGER NOT NULL DEFAULT 1,
    ms       INTEGER,
    grant_id INTEGER,                      -- core's oauth_grants row (a claude.ai connection)
    error    TEXT
);
CREATE INDEX mcp_calls_at ON mcp_calls(at);

-- AUTOINCREMENT: an answer's sources (assistant.db) point at their API call here across the files, so an id is
-- never handed out again after its row is purged.
CREATE TABLE api_calls (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    at               TEXT NOT NULL DEFAULT (datetime('now')),
    user_id          INTEGER,              -- NULL once that person is deleted
    via              TEXT NOT NULL CHECK (via IN ('chat', 'claude', 'test', 'apply')),
    api_id           INTEGER,              -- core's apis row
    operation_id     INTEGER,              -- core's api_operations row
    api_name         TEXT NOT NULL,
    operation_name   TEXT NOT NULL,
    params           TEXT NOT NULL DEFAULT '{}',
    status_code      INTEGER,
    ok               INTEGER NOT NULL CHECK (ok IN (0, 1)),
    cached           INTEGER NOT NULL DEFAULT 0 CHECK (cached IN (0, 1)),
    ms               INTEGER,
    error            TEXT,
    response_bytes   INTEGER,
    response_excerpt TEXT
);
CREATE INDEX api_calls_at ON api_calls(at);
CREATE INDEX api_calls_api ON api_calls(api_id, at);

CREATE TABLE agent_calls (
    id              INTEGER PRIMARY KEY,
    at              TEXT NOT NULL DEFAULT (datetime('now')),
    user_id         INTEGER,               -- deleted with that person (their tool arguments)
    conversation_id TEXT,
    tool            TEXT NOT NULL,
    args            TEXT NOT NULL DEFAULT '{}',
    file_ids        TEXT NOT NULL DEFAULT '[]',
    ok              INTEGER NOT NULL CHECK (ok IN (0, 1)),
    ms              INTEGER,
    error           TEXT
);
CREATE INDEX agent_calls_at ON agent_calls(at);
