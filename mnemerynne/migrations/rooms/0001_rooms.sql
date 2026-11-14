-- rooms.db: Rooms' tables, moved out of the core database (docs/plans/services-rewrite.md, Phase 3). The same
-- tables as core's migrations left them, without their references to core's users: SQLite can't follow a
-- reference into another file, so db.FEATURE_DBS['rooms'] lists those columns (user_columns: rows deleted with
-- the user; user_null_columns: kept, without the name) for forget_user and sweep_orphans.

CREATE TABLE chat_rooms (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('channel', 'dm', 'group_dm')),
    name        TEXT NOT NULL DEFAULT '',                  -- channels: lower-case, a-z 0-9 - _
    topic       TEXT NOT NULL DEFAULT '',
    visibility  TEXT NOT NULL DEFAULT 'open' CHECK (visibility IN ('open', 'private', 'group', 'direct')),
    group_ref   TEXT NOT NULL DEFAULT '',                  -- visibility 'group': group_refs value
    dm_key      TEXT UNIQUE,                               -- dm/group_dm: sorted member ids, "3,7"
    erynne      INTEGER NOT NULL DEFAULT 1 CHECK (erynne IN (0, 1)),   -- may answer @Erynne here
    created_by  INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    archived_at TEXT
);
CREATE UNIQUE INDEX chat_rooms_channel_name ON chat_rooms(name) WHERE kind = 'channel';

CREATE TABLE chat_members (
    room_id      INTEGER NOT NULL REFERENCES chat_rooms(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL,
    role         TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'member')),
    joined_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_read_id INTEGER NOT NULL DEFAULT 0,
    notify       TEXT NOT NULL DEFAULT 'all' CHECK (notify IN ('all', 'mentions', 'none')),
    hidden       INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),   -- left / closed the DM
    PRIMARY KEY (room_id, user_id)
);
CREATE INDEX chat_members_user ON chat_members(user_id);

CREATE TABLE chat_messages (
    id             INTEGER PRIMARY KEY,
    room_id        INTEGER NOT NULL REFERENCES chat_rooms(id) ON DELETE CASCADE,
    user_id        INTEGER,    -- NULL with via 'erynne': Erynne
    via            TEXT NOT NULL DEFAULT 'web' CHECK (via IN ('web', 'claude', 'chatgpt', 'erynne')),
    thread_root_id INTEGER REFERENCES chat_messages(id) ON DELETE CASCADE,
    body           TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    edited_at      TEXT,
    deleted_at     TEXT,
    pinned_at      TEXT,
    pinned_by      INTEGER
);
CREATE INDEX chat_messages_room ON chat_messages(room_id, thread_root_id, id);
CREATE INDEX chat_messages_thread ON chat_messages(thread_root_id, id);
CREATE INDEX chat_messages_at ON chat_messages(created_at);

CREATE TABLE chat_reactions (
    message_id INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL,
    emoji      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (message_id, user_id, emoji)
);

CREATE TABLE chat_attachments (
    id         INTEGER PRIMARY KEY,
    room_id    INTEGER NOT NULL REFERENCES chat_rooms(id) ON DELETE CASCADE,
    message_id INTEGER REFERENCES chat_messages(id) ON DELETE CASCADE,  -- NULL until the message is sent
    user_id    INTEGER,
    name       TEXT NOT NULL,
    mime       TEXT NOT NULL DEFAULT 'application/octet-stream',
    bytes      INTEGER NOT NULL,
    sha256     TEXT NOT NULL,
    rel_path   TEXT NOT NULL,                                           -- under <data_dir>/rooms
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX chat_attachments_message ON chat_attachments(message_id);

CREATE TABLE chat_mentions (
    message_id INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('user', 'channel', 'here')),
    PRIMARY KEY (message_id, user_id)
);
CREATE INDEX chat_mentions_user ON chat_mentions(user_id, message_id);

CREATE TABLE chat_events (
    id         INTEGER PRIMARY KEY,
    room_id    INTEGER NOT NULL REFERENCES chat_rooms(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX chat_events_at ON chat_events(created_at);
