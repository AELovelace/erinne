-- mnemerynne:no-fk
-- Claude can ask for edit access where it is refused (kind 'access', mcp_server.request_edit_access): the
-- person applies it, which switches AI edit on for a folder or file in their own File box (or clears a
-- read-only switch) with their own rights; for a whole library only an admin can apply it.
--
-- Restoring from the recycle bin is recorded (who and when), so list_changes can report it, and so is
-- the assistant renaming or moving a file (moved_via "claude:alice", moved_at = the updated_at it set), so
-- list_changes credits Claude instead of "someone in Files" until someone changes the file again.

ALTER TABLE files ADD COLUMN moved_via TEXT;
ALTER TABLE files ADD COLUMN moved_at TEXT;
ALTER TABLE files ADD COLUMN restored_at TEXT;
ALTER TABLE files ADD COLUMN restored_by INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE folders ADD COLUMN restored_at TEXT;
ALTER TABLE folders ADD COLUMN restored_by INTEGER REFERENCES users(id) ON DELETE SET NULL;
CREATE INDEX folders_deleted_at ON folders(collection_id, deleted_at);

CREATE TABLE agent_proposals_new (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT,
    message_id      INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('edit', 'create', 'mail', 'api', 'delete', 'access')),
    file_id         INTEGER REFERENCES files(id) ON DELETE CASCADE,
    collection_id   INTEGER REFERENCES collections(id) ON DELETE CASCADE,
    folder_id       INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    base_sha256     TEXT,
    new_sha256      TEXT,
    rel_path        TEXT,
    changes         TEXT NOT NULL DEFAULT '[]',
    diff            TEXT NOT NULL DEFAULT '',
    payload         TEXT,                      -- 'mail': the draft; 'api': {operation_id, params}; 'delete': {type, id};
                                               -- 'access': {type: file | folder | library, id}
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
