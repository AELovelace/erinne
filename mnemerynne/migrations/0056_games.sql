-- Games (mnemerynne/arcade.py): pages of the arcade's site that open from Media → Games, each with a name, a
-- short description, an icon (an emoji or a few letters) and its start page on the site. The site itself, the
-- switch and who may play are meta 'games_settings' (Access → Settings → Entertainment → Games).
CREATE TABLE games (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    icon        TEXT NOT NULL DEFAULT '🎮',
    start_path  TEXT NOT NULL DEFAULT '/',
    sort        INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
