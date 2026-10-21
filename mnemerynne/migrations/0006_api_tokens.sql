-- Personal API tokens (for Claude Desktop and other MCP clients) and a log of
-- every MCP tool call. Only a SHA-256 of each token is stored; the token itself
-- is shown once, when it is created.

CREATE TABLE api_tokens (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    prefix       TEXT NOT NULL,            -- first characters, so people can tell tokens apart
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at   TEXT,                     -- NULL = never
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX api_tokens_user ON api_tokens(user_id);

-- What was searched from Claude and which files' text went back.
CREATE TABLE mcp_calls (
    id       INTEGER PRIMARY KEY,
    at       TEXT NOT NULL DEFAULT (datetime('now')),
    user_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    token_id INTEGER REFERENCES api_tokens(id) ON DELETE SET NULL,
    tool     TEXT NOT NULL,
    args     TEXT NOT NULL,                -- JSON
    file_ids TEXT NOT NULL DEFAULT '[]',   -- JSON list of files whose text was returned
    ok       INTEGER NOT NULL DEFAULT 1,
    ms       INTEGER
);
CREATE INDEX mcp_calls_at ON mcp_calls(at);
