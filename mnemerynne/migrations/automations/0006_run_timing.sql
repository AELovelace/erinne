-- Run timing, for the Timeline tab (automations.timeline): how long an automation really takes, measured from when it
-- was meant to start until its last message went out.
--   scheduled_for: the time the scheduler claimed the run for (the automation's next_run_at then), UTC
--                  'YYYY-MM-DD HH:MM:SS'. NULL for Run now and Claude's run_automation (they start when asked).
--   delivered_at:  when delivery finished (Inbox, room, draft, webhooks all tried), UTC. NULL when nothing was sent:
--                  a quiet run, a failed or cancelled one, or one from before this migration.
-- A run's length is (delivered_at, else finished_at) minus (scheduled_for, else started_at).
ALTER TABLE automation_runs ADD COLUMN scheduled_for TEXT;
ALTER TABLE automation_runs ADD COLUMN delivered_at TEXT;
