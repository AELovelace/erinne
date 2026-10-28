-- Screen boxes (deploy/screen): whether a screen is the Fedora TV box rather than a TV paired from an ordinary
-- browser. Set when the box's start page opens the screen page as ?kiosk=1 (web/media.py screen_page); the
-- box's pages then get the small bar (Back to TV, ‹ ›) and listen for the remote on every page.

ALTER TABLE media_screens ADD COLUMN is_box INTEGER NOT NULL DEFAULT 0 CHECK (is_box IN (0, 1));
