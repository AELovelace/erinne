-- Media (mnemerynne/media/): a Kodi-style media centre over the share's Media library, off until an
-- admin turns it on (meta 'media_enabled'). The worker catalogs files under Media/Music, Media/Movies
-- and Media/Pictures into media_items; artwork files live in <media_cache_dir>/art. Live sources are
-- streams pulled by URL (rtmp://, HLS) and internet radio stations; the SGCRelay station directory is
-- read live and not stored here.

CREATE TABLE media_artwork (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('poster', 'fanart', 'cover', 'thumb')),
    rel_path   TEXT NOT NULL UNIQUE,                       -- under <media_cache_dir>/art
    source     TEXT NOT NULL CHECK (source IN ('local', 'embedded', 'frame', 'tmdb', 'caa')),
    width      INTEGER,
    height     INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE media_items (
    file_id       INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    section       TEXT NOT NULL CHECK (section IN ('music', 'movies', 'pictures')),
    media_type    TEXT NOT NULL CHECK (media_type IN ('audio', 'video', 'image')),
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
    album_key     TEXT,                                    -- music: media_albums.album_key
    track_no      INTEGER,
    disc_no       INTEGER,
    genre         TEXT NOT NULL DEFAULT '',
    taken_at      TEXT,                                    -- pictures: EXIF DateTimeOriginal, ISO
    camera        TEXT NOT NULL DEFAULT '',
    playback      TEXT NOT NULL DEFAULT 'direct' CHECK (playback IN ('direct', 'remux', 'audio', 'transcode')),
    artwork_id    INTEGER REFERENCES media_artwork(id) ON DELETE SET NULL,   -- poster / cover / thumb
    fanart_id     INTEGER REFERENCES media_artwork(id) ON DELETE SET NULL,   -- movies: backdrop
    overview      TEXT NOT NULL DEFAULT '',
    tmdb_id       INTEGER,
    mbid          TEXT,
    disk_mtime_ns INTEGER,                                 -- files.disk_mtime_ns when probed
    probed_at     TEXT NOT NULL DEFAULT (datetime('now')),
    scraped_at    TEXT,                                    -- online lookup done (found or not)
    error         TEXT
);
CREATE INDEX media_items_section ON media_items(section, sort_title);
CREATE INDEX media_items_album ON media_items(album_key) WHERE section = 'music';

-- Album art and MusicBrainz ids, per album (album_artist + album, lower-cased, "\x1f"-joined).
CREATE TABLE media_albums (
    album_key  TEXT PRIMARY KEY,
    artwork_id INTEGER REFERENCES media_artwork(id) ON DELETE SET NULL,
    mbid       TEXT,
    scraped_at TEXT
);

CREATE TABLE media_sources (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('rtmp', 'hls', 'radio')),
    name       TEXT NOT NULL,
    url        TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    sort       INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Where each person stopped in a movie or track, for Resume and "Continue watching".
CREATE TABLE media_progress (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    position_s REAL NOT NULL,
    duration_s REAL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, file_id)
);
