-- Which flow produced an assistant message: 'search' (document answer),
-- 'smalltalk', 'general', or later flows. NULL for messages before routing.
ALTER TABLE messages ADD COLUMN route TEXT;
