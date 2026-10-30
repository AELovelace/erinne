-- The Scratchpad page (web/scratchpad.py, docs/plans/scratchpad-page.md): people read their agents' boards and
-- write on them. A note from the person is a direction: author 'person' (set by the server from the browser
-- session; no tool can write one), for one agent label or everyone on the board, and it rides along with the
-- next connector call of each session it's for (scratchpad.directions_for).

ALTER TABLE scratchpad_notes ADD COLUMN author TEXT NOT NULL DEFAULT 'agent' CHECK (author IN ('agent', 'person'));
ALTER TABLE scratchpad_notes ADD COLUMN for_agent TEXT;                 -- an agent label; NULL: everyone on the board
ALTER TABLE scratchpad_notes ADD COLUMN reply_to INTEGER;               -- a note on the same board this one answers
ALTER TABLE scratchpad_notes ADD COLUMN urgent INTEGER NOT NULL DEFAULT 0 CHECK (urgent IN (0, 1));
CREATE INDEX scratchpad_person ON scratchpad_notes(user_id, author, status);

-- The person's sessions as the page shows them: one row per connection, agent label and board, touched on every
-- connector call (board '' until the session uses a board).
CREATE TABLE scratchpad_sessions (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connection TEXT NOT NULL,                        -- 't<token_id>' or 'g<grant_id>'
    agent      TEXT NOT NULL,                        -- the label it signs with
    board      TEXT NOT NULL DEFAULT '',
    last_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_tool  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, connection, agent, board)
);
CREATE INDEX scratchpad_sessions_last ON scratchpad_sessions(user_id, last_at);

-- Which agent was shown which direction (each sees it once), for "seen by … 2 min ago".
CREATE TABLE scratchpad_seen (
    note_id INTEGER NOT NULL REFERENCES scratchpad_notes(id) ON DELETE CASCADE,
    agent   TEXT NOT NULL,                           -- lower-case label
    seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (note_id, agent)
);

-- Notes that fell off the scratchpad (past scratchpad_days, a board cleared, a note deleted): kept here so they
-- can still be found later. Never purged.
CREATE TABLE scratchpad_archive (
    id          INTEGER PRIMARY KEY,                 -- the note's own id
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    board       TEXT NOT NULL,
    agent       TEXT NOT NULL,
    connection  TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL,
    status      TEXT NOT NULL,
    author      TEXT NOT NULL DEFAULT 'agent',
    for_agent   TEXT,
    reply_to    INTEGER,
    urgent      INTEGER NOT NULL DEFAULT 0,
    claimed_by  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    done_at     TEXT,
    archived_at TEXT NOT NULL DEFAULT (datetime('now')),
    reason      TEXT NOT NULL CHECK (reason IN ('expired', 'cleared', 'deleted'))
);
CREATE INDEX scratchpad_archive_user ON scratchpad_archive(user_id, board, id);
