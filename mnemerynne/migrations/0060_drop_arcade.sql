-- The arcade (0056: games from another site, proxied on a host of their own) was a failed experiment and is gone.
-- Its game list and its settings go with it. Media → Games now lists the built-in games (mnemerynne/games.py),
-- which need no table: they ship with the app.
DROP TABLE IF EXISTS games;
DELETE FROM meta WHERE key = 'games_settings';
