-- Lumi City (lumicity.py, Doll's design 2026-11-19, docs/plans/lumi-city.md): how big each town has grown, and who
-- owns which plot of land. games.db only: no references to core's users (db.GAMES_OWN_TABLES lists lumi_plots, so
-- forget_user deletes a gone account's row and the plot goes back to the office).

CREATE TABLE lumi_city_towns (
    town TEXT PRIMARY KEY CHECK (town IN ('left', 'right')),   -- LefTown or Right-City
    blocks INTEGER NOT NULL CHECK (blocks >= 1)                 -- how many blocks of 8 lots it has; grows at its gate, never shrinks
);
-- (seeded on demand with city.json's start_blocks: lumicity.towns, INSERT OR IGNORE)

CREATE TABLE lumi_plots (
    town TEXT NOT NULL CHECK (town IN ('left', 'right')),
    block INTEGER NOT NULL CHECK (block >= 1),                  -- 1 is next to Downtown, counting out toward the gate
    lot INTEGER NOT NULL CHECK (lot BETWEEN 1 AND 8),           -- city.json's template lots
    user_id INTEGER NOT NULL,                                   -- the owner; a lot with no row is office land, for sale
    style TEXT NOT NULL DEFAULT '',                             -- the house standing on it (an estate.json style id)
    yard TEXT NOT NULL DEFAULT '{}',                            -- JSON {slot: yard item id}: what stands in the yard
    paid INTEGER NOT NULL DEFAULT 0,                            -- what the owner paid for it, in coins
    bought_at INTEGER NOT NULL,                                 -- when the owner got it (milliseconds); part of its sales' ledger refs
    list_price INTEGER CHECK (list_price IS NULL OR list_price > 0),   -- for sale to other players at this price, or NULL
    listed_at INTEGER,                                          -- when it was listed (milliseconds), or NULL
    version INTEGER NOT NULL DEFAULT 1,                         -- goes up with every change, so a buyer's stale look is refused
    PRIMARY KEY (town, block, lot)
);
CREATE UNIQUE INDEX lumi_plots_one_each ON lumi_plots(user_id);               -- one plot per account
CREATE INDEX lumi_plots_listed ON lumi_plots(list_price) WHERE list_price IS NOT NULL;   -- the market's listings
