-- Each person's look: 'sakura' (the default) or 'neon' (theme-neon.css). Chosen on their Settings page.
ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT 'sakura';
