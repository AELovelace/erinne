-- Messages between people inside Mnemerynne (mnemerynne/messages.py): short notes one person
-- leaves another (also through Claude or Erynne, via 'claude'/'erynne'), and what Mnemerynne
-- itself tells someone (kind 'system', from_user_id NULL): a proposal an admin decided, a comment
-- on their file, a change to their account. Read on the Messages page; kept message_days.
-- (`messages` already holds chat turns, hence the name.)

CREATE TABLE user_messages (
    id           INTEGER PRIMARY KEY,
    from_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,     -- NULL: Mnemerynne itself
    to_user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('note', 'system')),
    via          TEXT NOT NULL CHECK (via IN ('web', 'claude', 'erynne', 'system')),
    body         TEXT NOT NULL,
    link         TEXT NOT NULL DEFAULT '',                              -- a path in Mnemerynne, or ''
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    read_at      TEXT
);
CREATE INDEX user_messages_inbox ON user_messages(to_user_id, read_at, id);
CREATE INDEX user_messages_sent ON user_messages(from_user_id, id);
CREATE INDEX user_messages_at ON user_messages(created_at);
