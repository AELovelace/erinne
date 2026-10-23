-- mnemerynne:no-fk
-- API actions (mnemerynne/apis.py): operations that change something (close a
-- ticket, acknowledge an alert). An action is never run by Erynne or Claude:
-- it becomes a proposal (kind 'api') that the person applies, and only then is
-- it called (api_calls.via 'apply'). Actions may use PUT, PATCH and DELETE;
-- reading operations stay GET or a fixed POST.

CREATE TABLE api_operations_new (
    id                INTEGER PRIMARY KEY,
    api_id            INTEGER NOT NULL REFERENCES apis(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    example_questions TEXT NOT NULL DEFAULT '',
    method            TEXT NOT NULL DEFAULT 'GET' CHECK (method IN ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')),
    path              TEXT NOT NULL,
    params            TEXT NOT NULL DEFAULT '[]',
    body              TEXT NOT NULL DEFAULT '',
    cache_seconds     INTEGER NOT NULL DEFAULT 60,
    enabled           INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    action            INTEGER NOT NULL DEFAULT 0 CHECK (action IN (0, 1)),   -- changes data: proposals only
    UNIQUE (api_id, name),
    CHECK (method <> 'POST' OR body <> ''),        -- a POST always has its fixed body; PUT may have none
    CHECK (action = 1 OR method IN ('GET', 'POST'))
);
INSERT INTO api_operations_new (id, api_id, name, description, example_questions, method, path, params, body,
                                cache_seconds, enabled)
SELECT id, api_id, name, description, example_questions, method, path, params, body, cache_seconds, enabled
FROM api_operations;
DROP TABLE api_operations;
ALTER TABLE api_operations_new RENAME TO api_operations;

CREATE TABLE api_calls_new (
    id               INTEGER PRIMARY KEY,
    at               TEXT NOT NULL DEFAULT (datetime('now')),
    user_id          INTEGER REFERENCES users(id) ON DELETE SET NULL,
    via              TEXT NOT NULL CHECK (via IN ('chat', 'claude', 'test', 'apply')),
    api_id           INTEGER REFERENCES apis(id) ON DELETE SET NULL,
    operation_id     INTEGER REFERENCES api_operations(id) ON DELETE SET NULL,
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
INSERT INTO api_calls_new SELECT * FROM api_calls;
DROP TABLE api_calls;
ALTER TABLE api_calls_new RENAME TO api_calls;
CREATE INDEX api_calls_at ON api_calls(at);
CREATE INDEX api_calls_api ON api_calls(api_id, at);

CREATE TABLE agent_proposals_new (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT,
    message_id      INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('edit', 'create', 'mail', 'api')),
    file_id         INTEGER REFERENCES files(id) ON DELETE CASCADE,
    collection_id   INTEGER REFERENCES collections(id) ON DELETE CASCADE,
    folder_id       INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    base_sha256     TEXT,
    new_sha256      TEXT,
    rel_path        TEXT,
    changes         TEXT NOT NULL DEFAULT '[]',
    diff            TEXT NOT NULL DEFAULT '',
    payload         TEXT,                      -- 'mail': the draft; 'api': {operation_id, params}
    result_url      TEXT,                      -- 'mail': the saved draft; 'api': the call in Mnemerynne
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'applied', 'dismissed', 'stale')),
    result_file_id  INTEGER REFERENCES files(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at      TEXT
);
INSERT INTO agent_proposals_new SELECT * FROM agent_proposals;
DROP TABLE agent_proposals;
ALTER TABLE agent_proposals_new RENAME TO agent_proposals;
CREATE INDEX agent_proposals_user ON agent_proposals(user_id, status);
CREATE INDEX agent_proposals_message ON agent_proposals(message_id);
