-- Internal APIs Erynne may query, read-only (mnemerynne/apis.py). Admins define
-- each API (address, credential, AD group) and the operations on it; people in
-- the API's group can ask about it in chat, and Claude only when the API's
-- Claude switch is on. Every call is recorded.

CREATE TABLE apis (
    id                 INTEGER PRIMARY KEY,
    name               TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description        TEXT NOT NULL DEFAULT '',          -- Markdown, shown on the APIs page
    base_url           TEXT NOT NULL,
    auth_type          TEXT NOT NULL DEFAULT 'none' CHECK (auth_type IN ('none', 'bearer', 'header', 'basic')),
    auth_header        TEXT NOT NULL DEFAULT '',          -- header name for 'header', e.g. X-API-Key
    auth_user          TEXT NOT NULL DEFAULT '',          -- user name for 'basic'
    secret_enc         BLOB,                              -- encrypted credential; never shown again
    secret_changed_at  TEXT,
    ca_pem             TEXT NOT NULL DEFAULT '',          -- trust only this certificate (self-signed servers)
    tls_check_hostname INTEGER NOT NULL DEFAULT 1 CHECK (tls_check_hostname IN (0, 1)),
    ad_group           TEXT NOT NULL DEFAULT '',          -- who may use it; empty = nobody yet
    claude_enabled     INTEGER NOT NULL DEFAULT 0 CHECK (claude_enabled IN (0, 1)),
    enabled            INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    spec               TEXT NOT NULL DEFAULT '',          -- imported documentation (Markdown or OpenAPI JSON)
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE api_operations (
    id                INTEGER PRIMARY KEY,
    api_id            INTEGER NOT NULL REFERENCES apis(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    example_questions TEXT NOT NULL DEFAULT '',           -- one per line; steer chat routing
    method            TEXT NOT NULL DEFAULT 'GET' CHECK (method IN ('GET', 'POST')),
    path              TEXT NOT NULL,                      -- may hold {param} placeholders
    params            TEXT NOT NULL DEFAULT '[]',         -- JSON list of parameter specs
    body              TEXT NOT NULL DEFAULT '',           -- fixed JSON body (POST only); never from the model
    cache_seconds     INTEGER NOT NULL DEFAULT 60,
    enabled           INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    UNIQUE (api_id, name),
    CHECK (method = 'GET' OR body <> '')
);

-- Example questions, embedded, for routing chat questions to an operation.
CREATE TABLE api_examples (
    id           INTEGER PRIMARY KEY,
    operation_id INTEGER NOT NULL REFERENCES api_operations(id) ON DELETE CASCADE,
    text         TEXT NOT NULL,
    embedding    BLOB NOT NULL
);
CREATE INDEX api_examples_operation ON api_examples(operation_id);

CREATE TABLE api_calls (
    id               INTEGER PRIMARY KEY,
    at               TEXT NOT NULL DEFAULT (datetime('now')),
    user_id          INTEGER REFERENCES users(id) ON DELETE SET NULL,
    via              TEXT NOT NULL CHECK (via IN ('chat', 'claude', 'test')),
    api_id           INTEGER REFERENCES apis(id) ON DELETE SET NULL,
    operation_id     INTEGER REFERENCES api_operations(id) ON DELETE SET NULL,
    api_name         TEXT NOT NULL,                       -- copied: the log still reads after a delete
    operation_name   TEXT NOT NULL,
    params           TEXT NOT NULL DEFAULT '{}',
    status_code      INTEGER,
    ok               INTEGER NOT NULL CHECK (ok IN (0, 1)),
    cached           INTEGER NOT NULL DEFAULT 0 CHECK (cached IN (0, 1)),
    ms               INTEGER,
    error            TEXT,
    response_bytes   INTEGER,
    response_excerpt TEXT                                 -- what the citation link shows
);
CREATE INDEX api_calls_at ON api_calls(at);
CREATE INDEX api_calls_api ON api_calls(api_id, at);

-- A chat answer can cite a live API response instead of a document chunk.
ALTER TABLE message_sources ADD COLUMN api_call_id INTEGER REFERENCES api_calls(id) ON DELETE SET NULL;
