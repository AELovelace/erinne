-- mnemerynne:no-fk
-- TV shows (mnemerynne/media/browse.py tv_shows): the catalog gets a 'tv' section (the TV folder inside
-- Media; each folder directly in it is one show, with its seasons in subfolders) and each episode its
-- season and episode number, read from names like 'Show S01E02.mkv' or a 'Season 2' folder. A show's
-- poster is kept once per show folder, as an album's cover is kept once per album.

-- media_items with the new section and the season/episode numbers (CHECK constraints can't be altered).
CREATE TABLE media_items_new (
    file_id       INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
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
INSERT INTO media_items_new (file_id, section, media_type, title, sort_title, year, duration_s, width, height, container,
    video_codec, audio_codec, bitrate, artist, album_artist, album, album_key, track_no, disc_no, genre, taken_at, camera,
    playback, artwork_id, fanart_id, overview, tmdb_id, mbid, pages, disk_mtime_ns, probed_at, scraped_at, error)
SELECT file_id, section, media_type, title, sort_title, year, duration_s, width, height, container,
    video_codec, audio_codec, bitrate, artist, album_artist, album, album_key, track_no, disc_no, genre, taken_at, camera,
    playback, artwork_id, fanart_id, overview, tmdb_id, mbid, pages, disk_mtime_ns, probed_at, scraped_at, error
FROM media_items;
DROP TABLE media_items;
ALTER TABLE media_items_new RENAME TO media_items;
CREATE INDEX media_items_section ON media_items(section, sort_title);
CREATE INDEX media_items_album ON media_items(album_key) WHERE section = 'music';
CREATE INDEX media_items_tv ON media_items(season_no, episode_no) WHERE section = 'tv';

-- One poster per show folder (a poster.jpg in it, or a frame from its first episode catalogued).
CREATE TABLE media_tv_shows (
    folder_id  INTEGER PRIMARY KEY REFERENCES folders(id) ON DELETE CASCADE,
    artwork_id INTEGER REFERENCES media_artwork(id) ON DELETE SET NULL
);
