-- media.db: Media's tables (the catalog, TVs, remotes, speakers, sources, progress and signage), moved out of
-- the core database (docs/plans/services-rewrite.md, Phase 4). The same tables as core's migrations left them,
-- without their references to core's users, files and folders: SQLite can't follow a reference into another
-- file, so db.FEATURE_DBS['media'] lists those columns for forget_user and sweep_orphans.

CREATE TABLE signage_groups (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX signage_groups_name ON signage_groups(lower(name));

CREATE TABLE media_artwork (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('poster', 'fanart', 'cover', 'thumb')),
    rel_path   TEXT NOT NULL UNIQUE,                       -- under <media_cache_dir>/art
    source     TEXT NOT NULL CHECK (source IN ('local', 'embedded', 'frame', 'tmdb', 'caa')),
    width      INTEGER,
    height     INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE media_screens (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT
, safe_x REAL NOT NULL DEFAULT 0 CHECK (safe_x BETWEEN 0 AND 10), safe_y REAL NOT NULL DEFAULT 0 CHECK (safe_y BETWEEN 0 AND 10), group_id INTEGER REFERENCES signage_groups(id) ON DELETE SET NULL, location TEXT NOT NULL DEFAULT '', orientation TEXT NOT NULL DEFAULT 'landscape'
    CHECK (orientation IN ('landscape', 'portrait')), last_state TEXT, last_state_at TEXT, player_version TEXT, effects TEXT, top_bar INTEGER NOT NULL DEFAULT 0 CHECK (top_bar IN (0, 1)), is_box INTEGER NOT NULL DEFAULT 0 CHECK (is_box IN (0, 1)), speaker INTEGER NOT NULL DEFAULT 0 CHECK (speaker IN (0, 1)), ui_scale REAL CHECK (ui_scale IS NULL OR ui_scale BETWEEN 0.75 AND 3), soundcloud_profile TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX media_screens_name ON media_screens(user_id, lower(name));

CREATE TABLE media_remotes (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT
, screen_id INTEGER REFERENCES media_screens(id) ON DELETE SET NULL);
CREATE UNIQUE INDEX media_remotes_name ON media_remotes(user_id, lower(name));

CREATE TABLE media_screen_guests (
    owner_id INTEGER NOT NULL,   -- whose TVs
    guest_id INTEGER NOT NULL,   -- whose remotes may drive them
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (owner_id, guest_id)
);
CREATE INDEX media_screen_guests_guest ON media_screen_guests(guest_id);

CREATE TABLE "media_sources" (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('rtmp', 'hls', 'radio', 'youtube')),
    name       TEXT NOT NULL,
    url        TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    sort       INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE media_albums (
    album_key  TEXT PRIMARY KEY,
    artwork_id INTEGER REFERENCES media_artwork(id) ON DELETE SET NULL,
    mbid       TEXT,
    scraped_at TEXT
);

CREATE TABLE "media_items" (
    file_id       INTEGER PRIMARY KEY,
    section       TEXT NOT NULL CHECK (section IN ('music', 'movies', 'tv', 'pictures', 'signage')),
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
    season_no     INTEGER,                                 -- TV: the season (0: Specials; NULL: not known)
    episode_no    INTEGER,                                 -- TV: the episode within its season
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
CREATE INDEX media_items_section ON media_items(section, sort_title);
CREATE INDEX media_items_album ON media_items(album_key) WHERE section = 'music';
CREATE INDEX media_items_tv ON media_items(season_no, episode_no) WHERE section = 'tv';
CREATE INDEX media_items_artwork ON media_items(artwork_id);
CREATE INDEX media_items_fanart ON media_items(fanart_id);

CREATE TABLE media_tv_shows (
    folder_id  INTEGER PRIMARY KEY,
    artwork_id INTEGER REFERENCES media_artwork(id) ON DELETE SET NULL
, tmdb_id INTEGER, overview TEXT NOT NULL DEFAULT '', year INTEGER, fanart_id INTEGER REFERENCES media_artwork(id) ON DELETE SET NULL, scraped_at TEXT);

CREATE TABLE media_progress (
    user_id    INTEGER NOT NULL,
    file_id    INTEGER NOT NULL,
    position_s REAL NOT NULL,
    duration_s REAL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, file_id)
);
CREATE INDEX media_progress_file ON media_progress(file_id);

CREATE TABLE signage_shows (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    seconds    INTEGER NOT NULL DEFAULT 10 CHECK (seconds BETWEEN 3 AND 3600),   -- per slide, unless it says
    shuffle    INTEGER NOT NULL DEFAULT 0 CHECK (shuffle IN (0, 1)),
    slides     TEXT NOT NULL DEFAULT '[]',
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
, description TEXT NOT NULL DEFAULT '', transition TEXT NOT NULL DEFAULT 'fade'
    CHECK (transition IN ('fade', 'cut', 'slide', 'zoom')), fit TEXT NOT NULL DEFAULT 'contain' CHECK (fit IN ('contain', 'cover', 'blur')), effects TEXT, updated_by INTEGER);
CREATE UNIQUE INDEX signage_shows_name ON signage_shows(lower(name));

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

CREATE TABLE signage_screens (
    screen_id    INTEGER PRIMARY KEY REFERENCES media_screens(id) ON DELETE CASCADE,
    idle_minutes INTEGER NOT NULL DEFAULT 5 CHECK (idle_minutes BETWEEN 1 AND 1440),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
