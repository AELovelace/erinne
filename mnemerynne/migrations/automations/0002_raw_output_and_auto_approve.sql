-- A run's raw output (automations_run.Trace): every step it took, as a JSON list, saved while it runs so the page
-- can show it live (GET /api/automations/runs/{id}/stream) and keep it afterwards ("Raw output"). Each step is
-- {"n": its number from 1, "at": unix seconds, "kind": ..., and that kind's fields}: the start and the prompt, each
-- tool call with its arguments and its result (trimmed), the model's notes between calls, the answer, where it was
-- delivered, and what went wrong. At most automations_run.TRACE_MAX steps; the last says when more were cut.
ALTER TABLE automation_runs ADD COLUMN trace TEXT NOT NULL DEFAULT '[]';

-- The proposals a run made (agent_proposals ids, in core; JSON list), so the Approvals tab can show them by run and
-- auto-approval knows which to decide.
ALTER TABLE automation_runs ADD COLUMN proposals TEXT NOT NULL DEFAULT '[]';

-- Auto-approval: an automation that may propose can also have its own proposals applied at the end of each run, as
-- its owner, with every check applying them by hand makes. Off unless the owner turns it on, per automation.
-- Proposals that wait for a Mnemerynne admin are never decided this way.
ALTER TABLE automations ADD COLUMN auto_approve INTEGER NOT NULL DEFAULT 0 CHECK (auto_approve IN (0, 1));
