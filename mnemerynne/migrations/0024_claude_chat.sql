-- Erynne on Claude (mnemerynne/claude_chat.py): each person's own Anthropic API
-- key, encrypted like API credentials and never shown again, and the Claude
-- model they picked. Only members of the Claude group (meta 'claude_group') can
-- save one or ask Claude in chat.
CREATE TABLE user_claude (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    key_enc    BLOB NOT NULL,
    model      TEXT NOT NULL DEFAULT 'claude-opus-5-5',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
