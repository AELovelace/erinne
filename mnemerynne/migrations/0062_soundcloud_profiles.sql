-- SoundCloud in Media (mnemerynne/media/soundcloud.py): the profile a person shows on their phone or computer, and the
-- one a screen shows (set in Kiosk settings or Media → Screens). Just the name after soundcloud.com/; '' is none.

ALTER TABLE users ADD COLUMN soundcloud_profile TEXT NOT NULL DEFAULT '';
ALTER TABLE media_screens ADD COLUMN soundcloud_profile TEXT NOT NULL DEFAULT '';
