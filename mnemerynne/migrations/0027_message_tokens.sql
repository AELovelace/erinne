-- Tokens each answer used (every model call made for it; graph.run_turn), for
-- chat's token counter: in and out, the external API's (Claude's) share, and
-- whether any of it was estimated because a model server didn't report usage.
ALTER TABLE messages ADD COLUMN tokens_in INTEGER;
ALTER TABLE messages ADD COLUMN tokens_out INTEGER;
ALTER TABLE messages ADD COLUMN tokens_external INTEGER;
ALTER TABLE messages ADD COLUMN tokens_estimated INTEGER NOT NULL DEFAULT 0;
