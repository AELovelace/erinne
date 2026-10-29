-- The agent scratchpad (mnemerynne/scratchpad.py): short notes a person's own agents (their Claude
-- Code sessions, Claude Desktop, claude.ai) leave for each other on named boards, for work that spans
-- sessions: "steps 1-3 done, step 4 next, leave the backup job alone". Only the person's own
-- connections see their boards. A note can be claimed by one agent (claimed_by) so two sessions don't
-- take the same task; a claim older than scratchpad_claim_minutes lapses. Notes untouched for
-- scratchpad_days are purged by retention.purge, open or not.

CREATE TABLE scratchpad_notes (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    board       TEXT NOT NULL,                       -- the board's key: lower-case, [a-z0-9._-], at most 60 chars
    agent       TEXT NOT NULL,                       -- who wrote it: the label the session gave, else its connection's name
    connection  TEXT NOT NULL DEFAULT '',            -- 't<token_id>' or 'g<grant_id>': which connection wrote it
    body        TEXT NOT NULL,                       -- Markdown, at most 4000 chars
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'superseded')),
    claimed_by  TEXT,                                -- an agent label while the note is claimed
    claimed_at  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),  -- any change: what `since` reads compare against
    done_at     TEXT
);
CREATE INDEX scratchpad_board ON scratchpad_notes(user_id, board, status, id);
CREATE INDEX scratchpad_updated ON scratchpad_notes(updated_at);
