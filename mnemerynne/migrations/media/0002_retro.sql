-- Retro (media/retro.py): console games from the Games folder on the share, played in the browser.
-- Saves and plays are kept per person and per game, where a game is the ROM's content (files.blob_sha256), not
-- its file: renaming, moving or adding a ROM again keeps its saves, and two copies of one ROM share them. The
-- rows outlive the ROM file on purpose (a save is the player's, not the share's); they go with their person
-- (db.FEATURE_DBS['media'].user_columns), since SQLite can't follow user_id into core.

CREATE TABLE retro_saves (
    user_id    INTEGER NOT NULL,
    rom_sha    TEXT NOT NULL,                     -- the ROM's sha256 (core's files.blob_sha256)
    -- 'sram': the game's own save (what the cartridge's battery kept); 'auto': the state taken on leaving and
    -- every few minutes of play, which Continue starts from; '1' to '4': the save state slots the player picks.
    slot       TEXT NOT NULL CHECK (slot IN ('sram', 'auto', '1', '2', '3', '4')),
    data       BLOB NOT NULL,
    shot       BLOB,                              -- a PNG of the screen at the time (states only)
    size       INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (user_id, rom_sha, slot)
);

CREATE TABLE retro_plays (
    user_id        INTEGER NOT NULL,
    rom_sha        TEXT NOT NULL,
    plays          INTEGER NOT NULL DEFAULT 0,
    last_played_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (user_id, rom_sha)
);
CREATE INDEX retro_plays_recent ON retro_plays(user_id, last_played_at);
