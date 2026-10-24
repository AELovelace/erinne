-- APIs that run one query at a time (e.g. SynchroHRM's direct access answers
-- HTTP 429 "Another query is still running"): apis.call queues calls to them
-- instead of sending them side by side, and Claude is told in list_apis.
ALTER TABLE apis ADD COLUMN one_at_a_time INTEGER NOT NULL DEFAULT 0 CHECK (one_at_a_time IN (0, 1));
