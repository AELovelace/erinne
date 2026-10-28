-- Media remotes (mnemerynne/media/remotes.py): a small touch panel (the ESP32 tablet, Mnemerynne-tab) that
-- controls its owner's screens. Paired with a code like a TV; after that it is known by its own key (sent
-- as a bearer token, stored here only as a SHA-256), which opens its owner's screens' remote and nothing
-- else, and never expires with sign-in sessions.

CREATE TABLE media_remotes (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT
);
CREATE UNIQUE INDEX media_remotes_name ON media_remotes(user_id, lower(name));
