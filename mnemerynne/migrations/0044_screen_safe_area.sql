-- Underscan for screens (mnemerynne/media/screens.py): how far in from the left and right (safe_x) and
-- the top and bottom (safe_y) a TV that crops its picture (overscan) needs everything drawn, as a
-- percentage of the width and height. Set on the TV with its calibration screen.

ALTER TABLE media_screens ADD COLUMN safe_x REAL NOT NULL DEFAULT 0 CHECK (safe_x BETWEEN 0 AND 10);
ALTER TABLE media_screens ADD COLUMN safe_y REAL NOT NULL DEFAULT 0 CHECK (safe_y BETWEEN 0 AND 10);
