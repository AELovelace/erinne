-- Lumi Pet's fights (mnemerynne/lumibattle.py). The whole fight is one JSON document; `turn` is the number the page
-- must send back with each button press, so a stale or repeated press changes nothing. One active fight per person,
-- as MommyBot's Touhou Trader kept one per player. Finished fights are dropped when the next one ends.

CREATE TABLE lumi_battles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,                 -- also names the win in the coin ledger, so it pays once
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    turn INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',                -- active, victory, defeat, ran
    created_at INTEGER NOT NULL                           -- milliseconds, like the pet's own times
);
CREATE UNIQUE INDEX lumi_battles_one_active ON lumi_battles(user_id) WHERE status = 'active';
