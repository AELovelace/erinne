-- mnemerynne:no-fk
-- Admin approvals: some proposals are decided by a Mnemerynne admin instead of the person who asked
-- (approver 'admin'): letting the AI edit a whole library when the person isn't an admin, and API
-- actions an admin has marked "needs admin approval" (api_operations.admin_approval). The person can
-- still withdraw theirs (dismiss); an admin approves it (applied, run on the person's behalf) or rejects
-- it with a note (rejected). decided_by and decision_note record who decided and why, so the person and
-- their Claude can see it (list_proposals / get_proposal).

ALTER TABLE api_operations ADD COLUMN admin_approval INTEGER NOT NULL DEFAULT 0 CHECK (admin_approval IN (0, 1));

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
                    CHECK (status IN ('pending', 'applied', 'dismissed', 'stale', 'rejected')),
    result_file_id  INTEGER REFERENCES files(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at      TEXT,
    approver        TEXT NOT NULL DEFAULT 'self' CHECK (approver IN ('self', 'admin')),
    decided_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    decision_note   TEXT
);
INSERT INTO agent_proposals_new (id, user_id, conversation_id, message_id, kind, file_id, collection_id, folder_id,
                                 name, base_sha256, new_sha256, rel_path, changes, diff, payload, result_url, status,
                                 result_file_id, created_at, decided_at)
SELECT id, user_id, conversation_id, message_id, kind, file_id, collection_id, folder_id, name, base_sha256,
       new_sha256, rel_path, changes, diff, payload, result_url, status, result_file_id, created_at, decided_at
FROM agent_proposals;
DROP TABLE agent_proposals;
ALTER TABLE agent_proposals_new RENAME TO agent_proposals;
CREATE INDEX agent_proposals_user ON agent_proposals(user_id, status);
CREATE INDEX agent_proposals_message ON agent_proposals(message_id);
CREATE INDEX agent_proposals_admin ON agent_proposals(approver, status);
