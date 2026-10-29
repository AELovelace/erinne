-- TV shows looked up online (mnemerynne/media/scrape.py), like films: a show's TMDB id, overview, first
-- year and backdrop, and when it was looked up; its episodes keep their names and overviews in media_items
-- (title, overview, tmdb_id, scraped_at), as films do.
ALTER TABLE media_tv_shows ADD COLUMN tmdb_id INTEGER;
ALTER TABLE media_tv_shows ADD COLUMN overview TEXT NOT NULL DEFAULT '';
ALTER TABLE media_tv_shows ADD COLUMN year INTEGER;
ALTER TABLE media_tv_shows ADD COLUMN fanart_id INTEGER REFERENCES media_artwork(id) ON DELETE SET NULL;
ALTER TABLE media_tv_shows ADD COLUMN scraped_at TEXT;
