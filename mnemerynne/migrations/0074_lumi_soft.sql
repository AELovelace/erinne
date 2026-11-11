-- Lumi-Soft (lumisoft.py): a scoped "airsoft" target gallery on the Mouse & keyboard shelf.

-- One run: what it is (story, a stage, the day's Daily Range or the Training Range), when it started (a replayed run
-- can't be longer than that allows), and how many continues have been paid for. Paid once, by ref "lumi-soft:<id>".
CREATE TABLE lumisoft_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mode TEXT NOT NULL,                                   -- training, story, stage or daily
    stage TEXT,                                           -- the stage's id for a stage run
    day INTEGER NOT NULL DEFAULT 0,                       -- a daily run's day number (it picks the scene)
    practice INTEGER NOT NULL DEFAULT 0,                  -- 1: a Daily Range retry or training: free, pays nothing
    started_at REAL NOT NULL,
    continues INTEGER NOT NULL DEFAULT 0,                 -- continues paid for so far
    finished INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX lumisoft_runs_user ON lumisoft_runs(user_id, finished);

-- Best scores: one row per person and board ("stage:garden", "story", "daily:<day>").
CREATE TABLE lumisoft_bests (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    board TEXT NOT NULL,
    score INTEGER NOT NULL,
    rank TEXT NOT NULL DEFAULT '',
    at REAL NOT NULL,
    PRIMARY KEY (user_id, board)
);
CREATE INDEX lumisoft_bests_board ON lumisoft_bests(board, score DESC);

-- What each person has done across runs (creatures popped, Daily Ranges played, stages cleared) and which Lumi-Soft
-- prizes they've won, as one JSON document.
CREATE TABLE lumisoft_progress (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    data TEXT NOT NULL
);
