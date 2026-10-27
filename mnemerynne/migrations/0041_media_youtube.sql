-- mnemerynne:no-fk
-- Media: YouTube as a kind of live source (a video, a playlist, or what a channel is streaming live),
-- played in YouTube's own embed player. `url` holds the embed address (media/live.py youtube_embed).
-- Rebuilds media_sources for the wider CHECK, so it runs with foreign keys off; db.migrate checks
-- them before commit.

CREATE TABLE media_sources_new (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('rtmp', 'hls', 'radio', 'youtube')),
    name       TEXT NOT NULL,
    url        TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    sort       INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO media_sources_new(id, kind, name, url, enabled, sort, created_by, created_at)
    SELECT id, kind, name, url, enabled, sort, created_by, created_at FROM media_sources;
DROP TABLE media_sources;
ALTER TABLE media_sources_new RENAME TO media_sources;
