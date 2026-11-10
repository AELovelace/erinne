-- Breakout and Snake (arcade.py, showdown.py), ported from Doll's GameMaker arcade.

-- Showdown tables: two racers and up to five watchers who can bet. The whole table is one JSON document, like
-- casino_rooms; who is still there is kept in memory (lobby.py), so only real changes are written.
CREATE TABLE arcade_rooms (
    code TEXT PRIMARY KEY,
    game TEXT NOT NULL,                                   -- breakout or snake
    state TEXT NOT NULL,
    updated_at REAL NOT NULL                              -- unix seconds of the last real change
);
CREATE INDEX arcade_rooms_game ON arcade_rooms(game, updated_at);

-- One run of a game: the seed the server handed out (so it can rebuild the levels or replay the moves) and when it
-- started (so a claimed score can be checked against the time it took). Paid once, by ref "<game>:<id>".
CREATE TABLE arcade_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    game TEXT NOT NULL,
    seed INTEGER NOT NULL,
    room TEXT,                                            -- the showdown table it races at; NULL for a solo run
    started_at REAL NOT NULL,
    finished INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX arcade_runs_user ON arcade_runs(user_id, game, finished);
