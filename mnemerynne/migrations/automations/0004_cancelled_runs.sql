-- A run can be cancelled (automations.cancel_run, automations_run.cancel): its owner stops it while it's queued,
-- running, waiting for a Claude routine's report, or holding a saved report not yet delivered. It then ends as
-- 'cancelled' (never 'failed'), and nothing that arrives later (a routine's report, the model's answer) changes it.
--
-- automation_runs is rebuilt (SQLite can't widen a CHECK): the same columns and rows, with 'cancelled' allowed.

CREATE TABLE automation_runs_new (
    id                 INTEGER PRIMARY KEY,
    automation_id      INTEGER NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    trigger            TEXT NOT NULL CHECK (trigger IN ('schedule', 'manual', 'mcp')),
    status             TEXT NOT NULL DEFAULT 'running'
                       CHECK (status IN ('queued', 'running', 'waiting', 'reported', 'done', 'quiet', 'failed',
                                         'timed_out', 'cancelled')),
    started_at         TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at        TEXT,
    output             TEXT NOT NULL DEFAULT '',
    sources            TEXT NOT NULL DEFAULT '[]',
    error              TEXT NOT NULL DEFAULT '',
    model              TEXT NOT NULL DEFAULT '',
    claude_session_url TEXT NOT NULL DEFAULT '',
    delivered          TEXT NOT NULL DEFAULT '[]',         -- JSON: where it went, and what failed
    trace              TEXT NOT NULL DEFAULT '[]',         -- JSON: the raw output, step by step (0002)
    proposals          TEXT NOT NULL DEFAULT '[]'          -- JSON: the agent_proposals ids it made (0002)
);
INSERT INTO automation_runs_new (id, automation_id, trigger, status, started_at, finished_at, output, sources, error,
                                 model, claude_session_url, delivered, trace, proposals)
    SELECT id, automation_id, trigger, status, started_at, finished_at, output, sources, error, model,
           claude_session_url, delivered, trace, proposals FROM automation_runs;
DROP TABLE automation_runs;
ALTER TABLE automation_runs_new RENAME TO automation_runs;
CREATE INDEX automation_runs_of ON automation_runs(automation_id, id);
CREATE INDEX automation_runs_status ON automation_runs(status);
