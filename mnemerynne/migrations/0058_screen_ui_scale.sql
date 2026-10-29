-- Text size for screens (mnemerynne/media/screens.py): how much bigger than usual a screen draws text and
-- controls (base.html, erynne.css), so a 4K TV is readable from the couch. NULL is automatic: from how wide the
-- screen is. Set on the TV with its Text size screen, or from the phone.

ALTER TABLE media_screens ADD COLUMN ui_scale REAL CHECK (ui_scale IS NULL OR ui_scale BETWEEN 0.75 AND 3);
