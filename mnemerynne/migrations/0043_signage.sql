-- Signage (mnemerynne/media/signage.py): slideshows admins make (pictures and clips from a Media
-- folder, single videos, text slides, web pages, the current announcements) and, per screen, when
-- each plays: rules by weekday and time of day, either once the screen has been idle a while or
-- always. Slides and rules are JSON the module checks; a screen's rules go with the screen.

CREATE TABLE signage_shows (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    seconds    INTEGER NOT NULL DEFAULT 10 CHECK (seconds BETWEEN 3 AND 3600),   -- per slide, unless it says
    shuffle    INTEGER NOT NULL DEFAULT 0 CHECK (shuffle IN (0, 1)),
    slides     TEXT NOT NULL DEFAULT '[]',
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX signage_shows_name ON signage_shows(lower(name));

CREATE TABLE signage_screens (
    screen_id    INTEGER PRIMARY KEY REFERENCES media_screens(id) ON DELETE CASCADE,
    idle_minutes INTEGER NOT NULL DEFAULT 5 CHECK (idle_minutes BETWEEN 1 AND 1440),
    rules        TEXT NOT NULL DEFAULT '[]',
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
