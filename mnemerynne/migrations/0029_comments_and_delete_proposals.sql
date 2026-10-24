-- mnemerynne:no-fk
-- Comments on files (mnemerynne/comments.py): people in Files and Claude through the connector leave
-- notes and replies on a file instead of editing each other's documents. A comment goes with its file.
--
-- Claude can ask to delete a file or folder (kind 'delete'): the person applies it, which moves it to
-- the recycle bin with their own rights, like API actions.

CREATE TABLE file_comments (
    id         INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    parent_id  INTEGER REFERENCES file_comments(id) ON DELETE CASCADE,   -- a reply to this comment
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    via        TEXT NOT NULL CHECK (via IN ('web', 'claude')),           -- written in Files, or by Claude for user_id
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX file_comments_file ON file_comments(file_id, id);
CREATE INDEX file_comments_at ON file_comments(created_at);

CREATE TABLE agent_proposals_new (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT,
    message_id      INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('edit', 'create', 'mail', 'api', 'delete')),
    file_id         INTEGER REFERENCES files(id) ON DELETE CASCADE,
    collection_id   INTEGER REFERENCES collections(id) ON DELETE CASCADE,
    folder_id       INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    base_sha256     TEXT,
    new_sha256      TEXT,
    rel_path        TEXT,
    changes         TEXT NOT NULL DEFAULT '[]',
    diff            TEXT NOT NULL DEFAULT '',
    payload         TEXT,                      -- 'mail': the draft; 'api': {operation_id, params}; 'delete': {type, id}
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
