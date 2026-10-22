-- OAuth for /mcp (mnemerynne/oauth.py): Claude on the web and phone (claude.ai
-- custom connectors) and Claude Code connect by signing the person in here and
-- asking their consent, instead of a pasted personal token. Only Claude's own
-- redirect addresses (and this computer's, for Claude Code) may register.
-- Tokens are stored as SHA-256 only, like personal tokens.

-- Apps that registered themselves (dynamic client registration).
CREATE TABLE oauth_clients (
    id            INTEGER PRIMARY KEY,
    client_id     TEXT NOT NULL UNIQUE,
    secret_hash   TEXT,                            -- NULL: a public client (PKCE only)
    name          TEXT NOT NULL,
    redirect_uris TEXT NOT NULL,                   -- JSON list, matched exactly
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at  TEXT
);

-- One-time codes between the consent page and the token request (minutes).
CREATE TABLE oauth_codes (
    code_hash      TEXT PRIMARY KEY,
    client_row     INTEGER NOT NULL REFERENCES oauth_clients(id) ON DELETE CASCADE,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    redirect_uri   TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    can_edit       INTEGER NOT NULL CHECK (can_edit IN (0, 1)),
    expires_at     TEXT NOT NULL,
    used_at        TEXT,
    grant_id       INTEGER                         -- the connection it made (ended if the code is used again)
);

-- A connection: one person allowed one app. Revoking it ends all its tokens.
CREATE TABLE oauth_grants (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_row   INTEGER NOT NULL REFERENCES oauth_clients(id) ON DELETE CASCADE,
    can_edit     INTEGER NOT NULL DEFAULT 0 CHECK (can_edit IN (0, 1)),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX oauth_grants_user ON oauth_grants(user_id);

-- Access tokens (an hour) and refresh tokens (90 days, replaced on each use;
-- using one twice ends the connection: it was copied).
CREATE TABLE oauth_tokens (
    token_hash TEXT PRIMARY KEY,
    grant_id   INTEGER NOT NULL REFERENCES oauth_grants(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
    expires_at TEXT NOT NULL,
    used_at    TEXT
);
CREATE INDEX oauth_tokens_grant ON oauth_tokens(grant_id);

ALTER TABLE mcp_calls ADD COLUMN grant_id INTEGER REFERENCES oauth_grants(id) ON DELETE SET NULL;
