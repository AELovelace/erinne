-- Lumi Launch (lumilaunch.py, Doll's break game 2026-11-14): a go's ticket, and each player's best distances.
-- games.db only: no references to core's users (db.FEATURE_DBS['games'].user_columns cleans these when a user goes).

CREATE TABLE lumi_launch_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,                 -- also names the payout in the coin ledger, so a go pays once
    user_id INTEGER NOT NULL,
    seed INTEGER NOT NULL,                                -- the course (lumilaunch_rules.piece)
    started_at REAL NOT NULL,                             -- the wall clock a replay's frames must fit inside
    finished INTEGER NOT NULL DEFAULT 0,
    score INTEGER NOT NULL DEFAULT 0,                     -- the replayed total, in metres
    paid INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX lumi_launch_runs_user ON lumi_launch_runs(user_id, finished);

CREATE TABLE lumi_launch_bests (
    user_id INTEGER PRIMARY KEY,
    best_go INTEGER NOT NULL DEFAULT 0,                   -- the longest go (three launches added up), in metres
    best_shot INTEGER NOT NULL DEFAULT 0,                 -- the longest single launch, in metres
    at REAL NOT NULL                                      -- when either best last moved
);
CREATE INDEX lumi_launch_bests_go ON lumi_launch_bests(best_go DESC);
