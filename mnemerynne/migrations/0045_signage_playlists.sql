-- mnemerynne:no-fk
-- Signage 0.2 (mnemerynne/media/signage.py): slideshows become playlists with a transition, a fit and
-- optional CRT effects; the per-screen JSON rules become a schedule table whose entries target every
-- screen, a screen group or one screen, with a priority and optional dates; screens get a group, a
-- location, an orientation and their last reported state (so "online" survives a web restart); the
-- catalog gets a 'signage' section (the Signage folder on the share: pictures, clips and PDFs, which
-- are 'document' items with a page count).

-- media_items with the new section, type and the page count (CHECK constraints can't be altered).
CREATE TABLE media_items_new (
    file_id       INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    section       TEXT NOT NULL CHECK (section IN ('music', 'movies', 'pictures', 'signage')),
    media_type    TEXT NOT NULL CHECK (media_type IN ('audio', 'video', 'image', 'document')),
    title         TEXT NOT NULL DEFAULT '',
    sort_title    TEXT NOT NULL DEFAULT '',
    year          INTEGER,
    duration_s    REAL,
    width         INTEGER,
    height        INTEGER,
    container     TEXT,
    video_codec   TEXT,
    audio_codec   TEXT,
    bitrate       INTEGER,
    artist        TEXT NOT NULL DEFAULT '',
    album_artist  TEXT NOT NULL DEFAULT '',
    album         TEXT NOT NULL DEFAULT '',
    album_key     TEXT,
    track_no      INTEGER,
    disc_no       INTEGER,
    genre         TEXT NOT NULL DEFAULT '',
    taken_at      TEXT,
    camera        TEXT NOT NULL DEFAULT '',
    playback      TEXT NOT NULL DEFAULT 'direct' CHECK (playback IN ('direct', 'remux', 'audio', 'transcode')),
    artwork_id    INTEGER REFERENCES media_artwork(id) ON DELETE SET NULL,
    fanart_id     INTEGER REFERENCES media_artwork(id) ON DELETE SET NULL,
    overview      TEXT NOT NULL DEFAULT '',
    tmdb_id       INTEGER,
    mbid          TEXT,
    pages         INTEGER,                                 -- documents (PDF): how many pages
    disk_mtime_ns INTEGER,
    probed_at     TEXT NOT NULL DEFAULT (datetime('now')),
    scraped_at    TEXT,
    error         TEXT
);
INSERT INTO media_items_new (file_id, section, media_type, title, sort_title, year, duration_s, width, height, container,
    video_codec, audio_codec, bitrate, artist, album_artist, album, album_key, track_no, disc_no, genre, taken_at, camera,
    playback, artwork_id, fanart_id, overview, tmdb_id, mbid, disk_mtime_ns, probed_at, scraped_at, error)
SELECT file_id, section, media_type, title, sort_title, year, duration_s, width, height, container,
    video_codec, audio_codec, bitrate, artist, album_artist, album, album_key, track_no, disc_no, genre, taken_at, camera,
    playback, artwork_id, fanart_id, overview, tmdb_id, mbid, disk_mtime_ns, probed_at, scraped_at, error
FROM media_items;
DROP TABLE media_items;
ALTER TABLE media_items_new RENAME TO media_items;
CREATE INDEX media_items_section ON media_items(section, sort_title);
CREATE INDEX media_items_album ON media_items(album_key) WHERE section = 'music';

-- Playlists (the signage_shows table keeps its name).
ALTER TABLE signage_shows ADD COLUMN description TEXT NOT NULL DEFAULT '';
ALTER TABLE signage_shows ADD COLUMN transition TEXT NOT NULL DEFAULT 'fade'
    CHECK (transition IN ('fade', 'cut', 'slide', 'zoom'));
ALTER TABLE signage_shows ADD COLUMN fit TEXT NOT NULL DEFAULT 'contain' CHECK (fit IN ('contain', 'cover', 'blur'));
ALTER TABLE signage_shows ADD COLUMN effects TEXT;        -- JSON (signage.Effects); NULL: follow the screen
ALTER TABLE signage_shows ADD COLUMN updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL;

-- Screen groups (lobby, bar, each property...), and what a screen is and does.
CREATE TABLE signage_groups (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX signage_groups_name ON signage_groups(lower(name));
ALTER TABLE media_screens ADD COLUMN group_id INTEGER REFERENCES signage_groups(id) ON DELETE SET NULL;
ALTER TABLE media_screens ADD COLUMN location TEXT NOT NULL DEFAULT '';
ALTER TABLE media_screens ADD COLUMN orientation TEXT NOT NULL DEFAULT 'landscape'
    CHECK (orientation IN ('landscape', 'portrait'));
ALTER TABLE media_screens ADD COLUMN last_state TEXT;     -- JSON, the last PUT /api/media/screen/state
ALTER TABLE media_screens ADD COLUMN last_state_at TEXT;
ALTER TABLE media_screens ADD COLUMN player_version TEXT;
ALTER TABLE media_screens ADD COLUMN effects TEXT;        -- JSON (signage.Effects); NULL: the Settings default

-- The schedule: which playlist plays where and when. A screen's own entry beats its group's, which beats
-- one for all screens; within those, the higher priority, then the newer entry. Days are the weekdays as
-- digits (0 Monday … 6 Sunday); start = end means all day, end before start runs past midnight; the
-- dates, when set, bound the entry.
CREATE TABLE signage_schedule (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    target     TEXT NOT NULL CHECK (target IN ('all', 'group', 'screen')),
    target_id  INTEGER,
    show_id    INTEGER NOT NULL REFERENCES signage_shows(id) ON DELETE CASCADE,
    days       TEXT NOT NULL DEFAULT '0123456',
    start      TEXT NOT NULL DEFAULT '00:00',
    "end"      TEXT NOT NULL DEFAULT '00:00',
    date_from  TEXT,
    date_to    TEXT,
    mode       TEXT NOT NULL DEFAULT 'idle' CHECK (mode IN ('idle', 'always')),
    priority   INTEGER NOT NULL DEFAULT 0,
    enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX signage_schedule_target ON signage_schedule(target, target_id);

-- The old per-screen rules become entries for that screen; the first rule won, so earlier ones rank higher.
INSERT INTO signage_schedule (target, target_id, show_id, days, start, "end", mode, priority)
SELECT 'screen', s.screen_id, json_extract(j.value, '$.show_id'),
       COALESCE((SELECT group_concat(d.value, '') FROM json_each(json_extract(j.value, '$.days')) d), '0123456'),
       COALESCE(json_extract(j.value, '$.start'), '00:00'), COALESCE(json_extract(j.value, '$.end'), '00:00'),
       COALESCE(json_extract(j.value, '$.mode'), 'idle'), 100 - j.key
FROM signage_screens s, json_each(s.rules) j
WHERE json_extract(j.value, '$.show_id') IN (SELECT id FROM signage_shows);
ALTER TABLE signage_screens DROP COLUMN rules;
