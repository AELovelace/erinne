-- Themes (mnemerynne/theme.py): every look is a row. Sakura and Neon are the two bundled stylesheets;
-- Corporate and Sad Girls Club are presets on the plain and neon bases, off until an admin turns them on.
-- config is JSON overrides on top of the base's stock values ('{}' = stock, or the preset for the two
-- presets, which live in Python so they can change between releases). users.theme holds the slug.

CREATE TABLE themes (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE CHECK (slug GLOB '[a-z0-9]*' AND length(slug) <= 40),
  name TEXT NOT NULL,
  base TEXT NOT NULL CHECK (base IN ('sakura', 'neon', 'plain')),
  builtin INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  pickable INTEGER NOT NULL DEFAULT 1,
  config TEXT NOT NULL DEFAULT '{}',
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);

INSERT INTO themes(slug, name, base, builtin, enabled) VALUES
  ('sakura', 'Sakura', 'sakura', 1, 1),
  ('neon', 'Neon', 'neon', 1, 1),
  ('corporate', 'Corporate', 'plain', 1, 0),
  ('sadgirlsclub', 'Sad Girls Club', 'neon', 1, 0);
