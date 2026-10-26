-- Claude's tool-call log keeps the error a failed call raised, so development diagnostics
-- (mnemerynne/diagnostics.py, the `diagnose` tool) can show what went wrong, not only that it did.

ALTER TABLE mcp_calls ADD COLUMN error TEXT;
