-- API templates admins upload (Access → APIs → Upload template): the same shape as
-- the built-in presets in apis.PRESETS, checked by apis.parse_template. A template
-- never holds a credential, certificate, AD group or Claude switch; those are set
-- on the API after it's made. Uploading a template with the same name replaces it.
CREATE TABLE api_templates (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    template    TEXT NOT NULL,                       -- the checked template, as JSON
    uploaded_by TEXT NOT NULL DEFAULT '',
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
