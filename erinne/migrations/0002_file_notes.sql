-- Non-fatal ingestion notes shown in the inbox (e.g. scanned pages that
-- could not be OCR'd), separate from `error`, which means the file failed.
ALTER TABLE files ADD COLUMN notes TEXT;

-- PDF units are segments: the part of one page that belongs to one bookmark
-- section, so a page with three subsections becomes three units.
ALTER TABLE blob_units ADD COLUMN page_no INTEGER;
