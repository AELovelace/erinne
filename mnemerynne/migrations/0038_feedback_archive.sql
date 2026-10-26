-- Every rating, kept for admins and training data (mnemerynne/feedback.py). A snapshot taken when the person
-- rates: the question, the answer, the sources' text and the agent's tool calls as they were then. No foreign
-- keys, on purpose: deleting the conversation, the user or a file, or re-indexing it, leaves the row as it is.
-- Append-only: changing a rating adds a row (the latest per message and user is the current one).

CREATE TABLE feedback_archive (
    id              INTEGER PRIMARY KEY,
    feedback_id     INTEGER,                 -- the feedback row it was taken from (SQLite may reuse the id later)
    rated_at        TEXT NOT NULL,
    archived_at     TEXT NOT NULL DEFAULT (datetime('now')),
    message_id      INTEGER NOT NULL,
    conversation_id TEXT,
    user_id         INTEGER,
    username        TEXT,
    rating          INTEGER NOT NULL CHECK (rating IN (-1, 1)),
    comment         TEXT,
    question        TEXT,
    question_scope  TEXT,
    answer          TEXT NOT NULL,
    answered_at     TEXT,
    status          TEXT,
    route           TEXT,
    model           TEXT,
    prompt_version  TEXT,
    sources         TEXT NOT NULL DEFAULT '[]',   -- JSON: each numbered source with its text then
    tool_calls      TEXT NOT NULL DEFAULT '[]'    -- JSON: the agent's tool calls for this answer
);
CREATE INDEX feedback_archive_rated ON feedback_archive(rated_at);
CREATE INDEX feedback_archive_message ON feedback_archive(message_id, user_id, id);
