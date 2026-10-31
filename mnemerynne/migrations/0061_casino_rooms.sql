-- Shared, server-authoritative play-chip tables. JSON includes private cards, never served directly.
CREATE TABLE casino_rooms (
    code TEXT PRIMARY KEY,
    game TEXT NOT NULL,
    state TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX casino_rooms_game ON casino_rooms(game, updated_at);
