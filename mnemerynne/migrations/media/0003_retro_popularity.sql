-- Retro's popularity (media/popularity.py): how many people have played each game on RetroAchievements, so the
-- library's first page can show each console's top 50. Only RetroAchievements' own data is kept here, and which
-- of the share's ROMs (by content, files.blob_sha256) is which of its games; nothing about anyone in the house.

-- RetroAchievements' list of a console's games, fetched again after a week (game data hardly changes).
CREATE TABLE retro_ra_lists (
    console       TEXT PRIMARY KEY,                -- media/retro.py's console key ('gba')
    ra_console_id INTEGER NOT NULL,                -- RetroAchievements' system id
    fetched_at    TEXT NOT NULL
);

-- Its games: their titles (matching by title where a ROM's hash isn't known) and player counts.
CREATE TABLE retro_ra_games (
    game_id    INTEGER PRIMARY KEY,                -- RetroAchievements' game id
    console    TEXT NOT NULL,
    title      TEXT NOT NULL,
    players    INTEGER,                            -- NumDistinctPlayers; NULL until asked
    players_at TEXT                                -- when it was asked (asked again after a month)
);

-- Every ROM hash RetroAchievements knows for a game (its own hashing rules: media/popularity.py ra_hash).
CREATE TABLE retro_ra_hashes (
    md5     TEXT PRIMARY KEY,
    game_id INTEGER NOT NULL
);
CREATE INDEX retro_ra_hashes_game ON retro_ra_hashes(game_id);

-- Which RetroAchievements game each ROM on the share is (game_id NULL: none found), and how it was found.
CREATE TABLE retro_ra_roms (
    rom_sha    TEXT PRIMARY KEY,                   -- the ROM's sha256 (core's files.blob_sha256)
    console    TEXT NOT NULL,
    md5        TEXT,                               -- its RetroAchievements hash, where one could be taken
    game_id    INTEGER,
    matched_by TEXT,                               -- 'hash' or 'title'
    checked_at TEXT NOT NULL
);
CREATE INDEX retro_ra_roms_game ON retro_ra_roms(game_id);
