-- Media screens (mnemerynne/media/screens.py): a browser left showing Media (the TV) that its owner
-- casts to from another device. Paired once by its owner while signed in; after that it is known by
-- its own key (a cookie on the TV, stored here only as a SHA-256), which opens Media and nothing
-- else, as its owner, and never expires with sign-in sessions. What a screen is playing lives in
-- the web app's memory only.

CREATE TABLE media_screens (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT
);
CREATE UNIQUE INDEX media_screens_name ON media_screens(user_id, lower(name));
