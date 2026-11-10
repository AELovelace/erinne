-- Lumi Pet's Stardust Garden (mnemerynne/lumigarden.py). One JSON document per person: every garden's plots and the
-- flowers in them (each with the moment it is ready), which flower kinds each garden has unlocked, the starpowder
-- waiting at the press and how far it has been squeezed towards the next coin, helper levels, cosmetics, permanent
-- upgrades, and cooldowns. Growing is only timestamps, so nothing here needs a worker to tick it.

CREATE TABLE lumi_gardens (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
