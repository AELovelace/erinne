-- Internal APIs (mnemerynne/apis.py), for the Wazuh expansion of 2026-11-17:
--
-- api_operations.shape: how a response is boiled down before anyone sees it (JSON, '' = as the API sent it).
--   "format": "elasticsearch" turns a _search answer into {total, rows, aggregations as plain lists},
--   "wazuh" turns the Wazuh manager's {data: {affected_items}} into {total, rows}; "keep" picks fields;
--   "rename" names them; "redact" hides passwords, keys and webhook addresses before the answer is cached,
--   logged or shown. Shaping happens on the server, so the raw answer never reaches the model.
-- apis.auth_login_path: for 'basic' APIs that sign in first (the Wazuh manager's
--   POST /security/user/authenticate): the user name and password are swapped there for a short-lived
--   token, which is then sent as a Bearer token. '' = send the user name and password on every call.

ALTER TABLE api_operations ADD COLUMN shape TEXT NOT NULL DEFAULT '';
ALTER TABLE apis ADD COLUMN auth_login_path TEXT NOT NULL DEFAULT '';
