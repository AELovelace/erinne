-- mnemerynne:no-fk
-- Proposals Claude can follow (1.0.1): every proposal carries a one-sentence summary (the same text
-- on the approve page and in Claude's answer) and an expiry (MNEMERYNNE_PROPOSAL_DAYS, 7); one nobody
-- decides in time becomes 'expired' instead of looking withdrawn. kind 'batch' holds several API
-- actions under one approval (payload.items, each with its own status).

CREATE TABLE agent_proposals_new (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT,
    message_id      INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('edit', 'create', 'mail', 'api', 'delete', 'access', 'batch')),
    file_id         INTEGER REFERENCES files(id) ON DELETE CASCADE,
    collection_id   INTEGER REFERENCES collections(id) ON DELETE CASCADE,
    folder_id       INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    base_sha256     TEXT,
    new_sha256      TEXT,
    rel_path        TEXT,
    changes         TEXT NOT NULL DEFAULT '[]',
    diff            TEXT NOT NULL DEFAULT '',
    payload         TEXT,
    result_url      TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'applied', 'dismissed', 'stale', 'rejected', 'expired')),
    result_file_id  INTEGER REFERENCES files(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at      TEXT,
    approver        TEXT NOT NULL DEFAULT 'self' CHECK (approver IN ('self', 'admin')),
    decided_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    decision_note   TEXT,
    summary         TEXT NOT NULL DEFAULT '',   -- one plain sentence: what will change
    expires_at      TEXT                         -- pending ones expire then (NULL: already decided)
);
INSERT INTO agent_proposals_new (id, user_id, conversation_id, message_id, kind, file_id, collection_id, folder_id,
                                 name, base_sha256, new_sha256, rel_path, changes, diff, payload, result_url, status,
                                 result_file_id, created_at, decided_at, approver, decided_by, decision_note, expires_at)
SELECT id, user_id, conversation_id, message_id, kind, file_id, collection_id, folder_id, name, base_sha256,
       new_sha256, rel_path, changes, diff, payload, result_url, status, result_file_id, created_at, decided_at,
       approver, decided_by, decision_note,
       CASE WHEN status = 'pending' THEN datetime(created_at, '+7 days') END
FROM agent_proposals;
DROP TABLE agent_proposals;
ALTER TABLE agent_proposals_new RENAME TO agent_proposals;
CREATE INDEX agent_proposals_user ON agent_proposals(user_id, status);
CREATE INDEX agent_proposals_message ON agent_proposals(message_id);
CREATE INDEX agent_proposals_admin ON agent_proposals(approver, status);
