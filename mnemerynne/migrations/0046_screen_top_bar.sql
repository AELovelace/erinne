-- Kiosk settings (mnemerynne/media/kiosk.py): whether a screen's page shows the top bar, so Ask, Files and the
-- other pages can be opened on the TV. Off by default; an admin changes it in the box's Kiosk settings.

ALTER TABLE media_screens ADD COLUMN top_bar INTEGER NOT NULL DEFAULT 0 CHECK (top_bar IN (0, 1));
