-- Lumi Pet's dungeon trips (mnemerynne/lumidungeon.py). A run is one JSON document: the floor's rooms, where Lumi
-- is, what she has found so far (her satchel) and any puzzle in progress. One run per person; it waits for as long as
-- it takes and picks up on any device, like a fight. A finished run stays until the next one starts, so the page can
-- show how it ended.

CREATE TABLE lumi_runs (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
