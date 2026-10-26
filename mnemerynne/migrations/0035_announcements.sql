-- Announcements (mnemerynne/notices.py): a notice an admin posts for everyone or for a group
-- (group_refs), shown as a card on every page until the person dismisses it ("Got it"), or, when
-- must_ack is set, acknowledges it. announcement_marks remembers who saw and acknowledged what.
-- Expired ones are removed by the worker after announcement_days.

CREATE TABLE announcements (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',          -- Markdown
    link        TEXT NOT NULL DEFAULT '',          -- a path in Mnemerynne, or ''
    group_ref   TEXT NOT NULL DEFAULT '',          -- group_refs; '' = everyone
    must_ack    INTEGER NOT NULL DEFAULT 0 CHECK (must_ack IN (0, 1)),
    expires_at  TEXT,                              -- NULL: until deleted
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX announcements_expires ON announcements(expires_at);

CREATE TABLE announcement_marks (
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    announcement_id INTEGER NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
    seen_at         TEXT,
    acked_at        TEXT,
    PRIMARY KEY (user_id, announcement_id)
);
