-- games.db: the built-in games' tables, moved out of the core database (docs/plans/services-rewrite.md,
-- Phase 2). The same tables as core migrations 0040-0074 left them, without their references to core's users:
-- SQLite can't follow a reference into another file, so db.FEATURE_DBS['games'].user_columns lists those
-- columns and forget_user / sweep_orphans clean them when a user goes. db.move_tables copies the rows over.

CREATE TABLE coin_accounts (
    id TEXT PRIMARY KEY,                                  -- 'user:<id>', or '__HOUSE__'
    user_id INTEGER UNIQUE,
    balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0 OR user_id IS NULL),   -- only the house may run short
    total_earned INTEGER NOT NULL DEFAULT 0,
    total_spent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE coin_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_account TEXT,                                    -- NULL: new coins (starting purse, refill, reward)
    to_account TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    type TEXT NOT NULL,                                   -- start, refill, reward, <game>_bet, <game>_win, <game>_refund
    game TEXT NOT NULL DEFAULT '',
    ref TEXT,                                             -- set when something must only ever pay once
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX coin_transactions_ref ON coin_transactions(ref) WHERE ref IS NOT NULL;
CREATE INDEX coin_transactions_to ON coin_transactions(to_account, created_at);

CREATE TABLE lumi_pets (
    user_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL,                                   -- the pet document; lumipet.py owns its shape
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE lumi_gardens (
    user_id INTEGER PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE lumi_runs (
    user_id INTEGER PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE lumi_battles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,                 -- also names the win in the coin ledger, so it pays once
    user_id INTEGER NOT NULL,
    state TEXT NOT NULL,
    turn INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',                -- active, victory, defeat, ran
    created_at INTEGER NOT NULL                           -- milliseconds, like the pet's own times
);
CREATE UNIQUE INDEX lumi_battles_one_active ON lumi_battles(user_id) WHERE status = 'active';

CREATE TABLE lumi_push_events (
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,                                   -- hunger, energy, joy, nap
    episode TEXT NOT NULL,                                -- when this need began
    created_at INTEGER NOT NULL,                          -- milliseconds, like the pet's own times
    sent_at INTEGER,                                      -- NULL: waiting (quiet hours, or one was sent recently)
    PRIMARY KEY (user_id, kind, episode)
);

CREATE TABLE lumi_parties (
    code TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at REAL NOT NULL                              -- unix seconds of the last real change
);

CREATE TABLE lumi_party_members (
    user_id INTEGER PRIMARY KEY,
    code TEXT NOT NULL REFERENCES lumi_parties(code) ON DELETE CASCADE
);
CREATE INDEX lumi_party_members_code ON lumi_party_members(code);

CREATE TABLE push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    endpoint TEXT NOT NULL UNIQUE,                        -- the push service's address for this browser
    p256dh TEXT NOT NULL,                                 -- the browser's public key (base64url)
    auth TEXT NOT NULL,                                   -- the browser's shared secret (base64url)
    utc_offset INTEGER NOT NULL DEFAULT 0,                -- minutes east of UTC when it last subscribed, for quiet hours
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX push_subscriptions_user ON push_subscriptions(user_id);

CREATE TABLE arcade_rooms (
    code TEXT PRIMARY KEY,
    game TEXT NOT NULL,                                   -- breakout or snake
    state TEXT NOT NULL,
    updated_at REAL NOT NULL                              -- unix seconds of the last real change
);
CREATE INDEX arcade_rooms_game ON arcade_rooms(game, updated_at);

CREATE TABLE arcade_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    game TEXT NOT NULL,
    seed INTEGER NOT NULL,
    room TEXT,                                            -- the showdown table it races at; NULL for a solo run
    started_at REAL NOT NULL,
    finished INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX arcade_runs_user ON arcade_runs(user_id, game, finished);

CREATE TABLE casino_rooms (
    code TEXT PRIMARY KEY,
    game TEXT NOT NULL,
    state TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX casino_rooms_game ON casino_rooms(game, updated_at);

CREATE TABLE prism_runs (
    user_id INTEGER PRIMARY KEY,
    drops INTEGER NOT NULL DEFAULT 0,
    score INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE runner_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    started_at REAL NOT NULL                              -- seconds since 1970, by the server's clock
);
CREATE INDEX runner_runs_user ON runner_runs(user_id);

CREATE TABLE lumisoft_progress (
    user_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL
);

CREATE TABLE lumisoft_bests (
    user_id INTEGER NOT NULL,
    board TEXT NOT NULL,
    score INTEGER NOT NULL,
    rank TEXT NOT NULL DEFAULT '',
    at REAL NOT NULL,
    PRIMARY KEY (user_id, board)
);
CREATE INDEX lumisoft_bests_board ON lumisoft_bests(board, score DESC);

CREATE TABLE lumisoft_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    mode TEXT NOT NULL,                                   -- training, story, stage or daily
    stage TEXT,                                           -- the stage's id for a stage run
    day INTEGER NOT NULL DEFAULT 0,                       -- a daily run's day number (it picks the scene)
    practice INTEGER NOT NULL DEFAULT 0,                  -- 1: a Daily Range retry or training: free, pays nothing
    started_at REAL NOT NULL,
    continues INTEGER NOT NULL DEFAULT 0,                 -- continues paid for so far
    finished INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX lumisoft_runs_user ON lumisoft_runs(user_id, finished);
