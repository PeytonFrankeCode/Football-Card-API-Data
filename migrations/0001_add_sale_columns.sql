-- Adds richer sales columns introduced alongside the scraper grade-parsing work.
-- Applied to the live D1 database via `wrangler d1 migrations apply gridironcards --remote`.
-- The migrations framework tracks applied files in d1_migrations, so this runs
-- exactly once even though SQLite has no "ADD COLUMN IF NOT EXISTS".
ALTER TABLE sales ADD COLUMN grade_company TEXT;
ALTER TABLE sales ADD COLUMN seller TEXT;
ALTER TABLE sales ADD COLUMN item_number TEXT;
