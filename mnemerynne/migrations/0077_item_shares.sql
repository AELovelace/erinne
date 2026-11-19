-- Files and folders shared inside Mnemerynne (mnemerynne/sharing.py): with an account, a Mnemerynne group, a
-- domain group, every active account, or anyone holding a link. Only Mnemerynne honours these; the share's own
-- permissions on disk (what Explorer shows) are never touched.
CREATE TABLE item_shares (
    id           INTEGER PRIMARY KEY,
    file_id      INTEGER REFERENCES files(id) ON DELETE CASCADE,      -- exactly one of file_id / folder_id
    folder_id    INTEGER REFERENCES folders(id) ON DELETE CASCADE,    -- a folder shares everything inside it
    kind         TEXT NOT NULL CHECK (kind IN ('user', 'group', 'ad_group', 'everyone', 'link')),
    user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,      -- kind 'user'
    group_id     INTEGER REFERENCES groups(id) ON DELETE CASCADE,     -- kind 'group' (Mnemerynne's groups)
    ad_group     TEXT COLLATE NOCASE,                                  -- kind 'ad_group' (a domain group's name)
    can_write    INTEGER NOT NULL DEFAULT 0 CHECK (can_write IN (0, 1)),
    created_by   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,   -- shares last while they may
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at   TEXT,                                                 -- NULL: until removed
    -- kind 'link': the token in the URL is HMAC(secret key, link_nonce). It is never stored: the app rebuilds it
    -- to show the link again, and a copy of the database alone can't make one. link_hash (its SHA-256) is what
    -- /s/<token> looks up.
    link_nonce   TEXT,
    link_hash    TEXT UNIQUE,
    last_used_at TEXT,
    CHECK ((file_id IS NULL) <> (folder_id IS NULL)),
    CHECK ((kind = 'user') = (user_id IS NOT NULL)),
    CHECK ((kind = 'group') = (group_id IS NOT NULL)),
    CHECK ((kind = 'ad_group') = (ad_group IS NOT NULL)),
    CHECK ((kind = 'link') = (link_hash IS NOT NULL AND link_nonce IS NOT NULL)),
    CHECK (kind <> 'link' OR can_write = 0)                            -- links are always view-only
);
CREATE INDEX item_shares_file ON item_shares(file_id) WHERE file_id IS NOT NULL;
CREATE INDEX item_shares_folder ON item_shares(folder_id) WHERE folder_id IS NOT NULL;
CREATE INDEX item_shares_user ON item_shares(user_id) WHERE user_id IS NOT NULL;
-- One share per item and person/group (sharing again changes it); any number of links.
CREATE UNIQUE INDEX item_shares_one_each ON item_shares(
    COALESCE(file_id, 0), COALESCE(folder_id, 0), kind, COALESCE(user_id, 0), COALESCE(group_id, 0),
    COALESCE(ad_group, '')) WHERE kind <> 'link';
