-- GridironCards D1 schema

CREATE TABLE IF NOT EXISTS players (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  name     TEXT NOT NULL,
  team     TEXT,
  position TEXT
);

CREATE TABLE IF NOT EXISTS cards (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  player_id    INTEGER NOT NULL,
  year         INTEGER NOT NULL,
  brand        TEXT    NOT NULL,
  set_name     TEXT,
  card_number  TEXT,
  variant      TEXT,
  is_rookie    INTEGER DEFAULT 0,
  is_autograph INTEGER DEFAULT 0,
  is_patch     INTEGER DEFAULT 0,
  print_run    INTEGER,
  FOREIGN KEY (player_id) REFERENCES players(id)
);

CREATE TABLE IF NOT EXISTS sales (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id     INTEGER NOT NULL,
  sale_price  REAL    NOT NULL,
  sale_date   TEXT    NOT NULL,
  platform    TEXT,
  condition   TEXT,
  grade       REAL,
  notes       TEXT,
  listing_url TEXT UNIQUE,
  created_at  TEXT DEFAULT (datetime('now')),
  -- Columns added later live in migrations/ (applied via `wrangler d1 migrations apply`):
  --   0001 → grade_company, seller, item_number
  FOREIGN KEY (card_id) REFERENCES cards(id)
);

-- Popularity counter so the cron pre-warm job knows which searches to refresh.
CREATE TABLE IF NOT EXISTS query_stats (
  query   TEXT PRIMARY KEY,
  hits    INTEGER DEFAULT 0,
  last_at TEXT
);

CREATE TABLE IF NOT EXISTS search_cache (
  query       TEXT PRIMARY KEY,
  results_json TEXT NOT NULL,
  cached_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email         TEXT UNIQUE NOT NULL,
  key           TEXT UNIQUE NOT NULL,
  is_active     INTEGER DEFAULT 1,
  request_count INTEGER DEFAULT 0,
  created_at    TEXT DEFAULT (datetime('now'))
);
