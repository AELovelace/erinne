-- A prompt file: an automation's instructions can live in a .md or .txt file in Mnemerynne instead of the "What to
-- do" box (automations.prompt_for_run). Each run reads the file fresh, as its owner, so editing the file changes the
-- automation from its next run, and Versions on the file is its history. When the file has a fenced code block, the
-- first one is the instructions (the rest of the file can explain them); otherwise the whole file is.
-- NULL = the typed prompt, as before. The typed prompt is kept either way, so switching back loses nothing.
-- No reference into core's files (another database file): a file that's gone fails the run with a clear message.
ALTER TABLE automations ADD COLUMN prompt_file_id INTEGER;
