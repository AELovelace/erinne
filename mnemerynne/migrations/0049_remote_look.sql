-- Remote apps' look (mnemerynne/remote.py): an XML app's pages can be drawn with a stylesheet bundled
-- with Mnemerynne instead of the app's own. 'rdweb' is Erynne's look for RD Web Access; NULL keeps the
-- app's. look_theme fixes that look's theme ('default', 'neon'); '' follows the viewer's.

ALTER TABLE remote_apps ADD COLUMN xsl_override TEXT CHECK (xsl_override IN ('rdweb'));
ALTER TABLE remote_apps ADD COLUMN look_theme TEXT NOT NULL DEFAULT '' CHECK (look_theme IN ('', 'default', 'neon'));
