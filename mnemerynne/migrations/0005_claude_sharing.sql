-- Claude sharing and an access audit log.
-- A collection can be searched from Claude (the MCP endpoint) only when its
-- switch is on (collections.claude_enabled) AND, for a library, the person's
-- grant, direct or through a group, allows it (collection_grants.can_claude).
-- An inbox needs only its own switch: its owner is the only one who reads it.
-- Both default to on (all libraries and inboxes may go to Claude for now);
-- admins switch them off per library, grant or inbox.

ALTER TABLE collections ADD COLUMN claude_enabled INTEGER NOT NULL DEFAULT 1 CHECK (claude_enabled IN (0, 1));
ALTER TABLE collections ADD COLUMN claude_changed_by INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE collections ADD COLUMN claude_changed_at TEXT;

ALTER TABLE collection_grants ADD COLUMN can_claude INTEGER NOT NULL DEFAULT 1 CHECK (can_claude IN (0, 1));

-- Who changed access, and when. Names are copied in so the log still reads
-- after a user, group or library is deleted.
CREATE TABLE access_log (
    id         INTEGER PRIMARY KEY,
    at         TEXT NOT NULL DEFAULT (datetime('now')),
    actor_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    actor_name TEXT NOT NULL,
    action     TEXT NOT NULL,          -- e.g. 'grant', 'claude.enable', 'group.members'
    target     TEXT NOT NULL,          -- library, group or inbox owner
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX access_log_at ON access_log(at);
