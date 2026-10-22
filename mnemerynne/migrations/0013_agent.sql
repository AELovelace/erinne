-- mnemerynne:no-fk
-- Erynne using the tools in chat (mnemerynne/agent.py), for members of the AD
-- group picked on Settings (meta 'agent_group'; the agent model's address is
-- meta 'agent_model'). Erynne never changes a file by herself: an edit or a
-- new file is a proposal, held here with its content under
-- <data_dir>/proposals, until the person presses Apply (or Dismiss).

CREATE TABLE agent_proposals (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT,
    message_id      INTEGER REFERENCES messages(id) ON DELETE SET NULL,   -- the answer it appeared under
    kind            TEXT NOT NULL CHECK (kind IN ('edit', 'create')),
    file_id         INTEGER REFERENCES files(id) ON DELETE CASCADE,       -- 'edit': the file to change
    collection_id   INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    folder_id       INTEGER REFERENCES folders(id) ON DELETE CASCADE,     -- 'create': where it goes
    name            TEXT NOT NULL,
    base_sha256     TEXT,                                                 -- 'edit': the version it was made from
    new_sha256      TEXT NOT NULL,
    rel_path        TEXT NOT NULL,
    changes         TEXT NOT NULL DEFAULT '[]',                           -- JSON list, shown on the card
    diff            TEXT NOT NULL DEFAULT '',                             -- text files: unified diff
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'applied', 'dismissed', 'stale')),
    result_file_id  INTEGER REFERENCES files(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at      TEXT
);
CREATE INDEX agent_proposals_user ON agent_proposals(user_id, status);
CREATE INDEX agent_proposals_message ON agent_proposals(message_id);

-- Every tool call Erynne makes, like mcp_calls for Claude.
CREATE TABLE agent_calls (
    id              INTEGER PRIMARY KEY,
    at              TEXT NOT NULL DEFAULT (datetime('now')),
    user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    conversation_id TEXT,
    tool            TEXT NOT NULL,
    args            TEXT NOT NULL DEFAULT '{}',
    file_ids        TEXT NOT NULL DEFAULT '[]',
    ok              INTEGER NOT NULL CHECK (ok IN (0, 1)),
    ms              INTEGER,
    error           TEXT
);
CREATE INDEX agent_calls_at ON agent_calls(at);

-- Versions kept when Erynne's proposal is applied.
CREATE TABLE file_versions_new (
    id         INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    sha256     TEXT NOT NULL,
    size       INTEGER NOT NULL,
    rel_path   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    via        TEXT NOT NULL CHECK (via IN ('claude', 'restore', 'erynne')),
    token_id   INTEGER REFERENCES api_tokens(id) ON DELETE SET NULL,
    summary    TEXT NOT NULL DEFAULT ''
);
INSERT INTO file_versions_new SELECT id, file_id, sha256, size, rel_path, created_at, created_by, via, token_id, summary
FROM file_versions;
DROP TABLE file_versions;
ALTER TABLE file_versions_new RENAME TO file_versions;
CREATE INDEX file_versions_file ON file_versions(file_id, id);
