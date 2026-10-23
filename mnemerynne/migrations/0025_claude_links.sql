-- Links Claude can fetch (mnemerynne/claude_links.py): when a tool result
-- through /mcp points at a file, it also carries a short-lived address that
-- Claude's web fetch can open without signing in. Each is tied to the person,
-- their Claude connection (token or OAuth grant) and the file, and is checked
-- again on every fetch. Settings (on/off, minutes) are in meta 'claude_links'.
CREATE TABLE claude_links (
    id              TEXT PRIMARY KEY,                -- random; the address
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_id        INTEGER REFERENCES api_tokens(id) ON DELETE CASCADE,
    grant_id        INTEGER REFERENCES oauth_grants(id) ON DELETE CASCADE,
    file_id         INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT NOT NULL,
    fetches         INTEGER NOT NULL DEFAULT 0,
    last_fetched_at TEXT,
    CHECK (token_id IS NOT NULL OR grant_id IS NOT NULL)
);
CREATE INDEX claude_links_reuse ON claude_links(user_id, file_id, expires_at);
