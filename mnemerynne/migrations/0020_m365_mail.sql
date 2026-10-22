-- mnemerynne:no-fk
-- Mail and calendar for Erynne's agent group (mnemerynne/m365_mail.py):
-- shared mailboxes admins list per tenant, and reply drafts as proposals
-- (kind 'mail') that the person saves to Drafts with a click. Nothing sends.

CREATE TABLE m365_shared_mailboxes (
    id         INTEGER PRIMARY KEY,
    tenant_row INTEGER NOT NULL REFERENCES m365_tenants(id) ON DELETE CASCADE,
    address    TEXT NOT NULL COLLATE NOCASE,
    label      TEXT NOT NULL,                  -- what Erynne and people call it, e.g. "Front desk"
    group_ref  TEXT NOT NULL DEFAULT '',       -- group_refs: who may ask about it ('' = anyone with access)
    UNIQUE (tenant_row, address)
);

-- Proposals that aren't files: a mail draft has no collection or bytes, only a payload.
CREATE TABLE agent_proposals_new (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT,
    message_id      INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('edit', 'create', 'mail')),
    file_id         INTEGER REFERENCES files(id) ON DELETE CASCADE,
    collection_id   INTEGER REFERENCES collections(id) ON DELETE CASCADE,
    folder_id       INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    base_sha256     TEXT,
    new_sha256      TEXT,
    rel_path        TEXT,
    changes         TEXT NOT NULL DEFAULT '[]',
    diff            TEXT NOT NULL DEFAULT '',
    payload         TEXT,                      -- kind 'mail': {identity_id, to, cc, subject, body, reply_to, reply_all}
    result_url      TEXT,                      -- kind 'mail': the saved draft in Outlook on the web
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'applied', 'dismissed', 'stale')),
    result_file_id  INTEGER REFERENCES files(id) ON DELETE SET NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at      TEXT
);
INSERT INTO agent_proposals_new (id, user_id, conversation_id, message_id, kind, file_id, collection_id, folder_id,
                                 name, base_sha256, new_sha256, rel_path, changes, diff, status, result_file_id,
                                 created_at, decided_at)
SELECT id, user_id, conversation_id, message_id, kind, file_id, collection_id, folder_id, name, base_sha256,
       new_sha256, rel_path, changes, diff, status, result_file_id, created_at, decided_at FROM agent_proposals;
DROP TABLE agent_proposals;
ALTER TABLE agent_proposals_new RENAME TO agent_proposals;
CREATE INDEX agent_proposals_user ON agent_proposals(user_id, status);
CREATE INDEX agent_proposals_message ON agent_proposals(message_id);

-- Sources that aren't Mnemerynne files or API calls (an email, an event, a
-- live SharePoint hit): what the citation showed and where it pointed.
ALTER TABLE message_sources ADD COLUMN ext_kind TEXT;
ALTER TABLE message_sources ADD COLUMN ext_title TEXT;
ALTER TABLE message_sources ADD COLUMN ext_location TEXT;
ALTER TABLE message_sources ADD COLUMN ext_url TEXT;
