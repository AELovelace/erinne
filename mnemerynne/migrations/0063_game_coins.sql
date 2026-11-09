-- One purse per account for the Media games (mnemerynne/coins.py), built like LumiBot's SadGirlCoin but kept on this
-- server: balances, a permanent list of every movement, and a house account that bets go to and wins come from.

CREATE TABLE coin_accounts (
    id TEXT PRIMARY KEY,                                  -- 'user:<id>', or '__HOUSE__'
    user_id INTEGER UNIQUE REFERENCES users(id) ON DELETE CASCADE,
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

INSERT INTO coin_accounts(id, balance, total_earned) VALUES ('__HOUSE__', 1000000, 1000000);

-- A Prism Drop game in progress: its drops are rolled on the server, so a game's score can pay coins.
CREATE TABLE prism_runs (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    drops INTEGER NOT NULL DEFAULT 0,
    score INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seats saved before this hold table-only chips that mean nothing to a purse, so open tables close.
DELETE FROM casino_rooms;
