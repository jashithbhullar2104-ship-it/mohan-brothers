-- D1 schema for Mohan Brothers enquiries.
-- Run this once in the Cloudflare dashboard:  D1 -> your database -> Console.

CREATE TABLE IF NOT EXISTS enquiries (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at  TEXT    NOT NULL,
  name        TEXT    NOT NULL,
  company     TEXT    NOT NULL,
  phone       TEXT    NOT NULL,
  email       TEXT,
  service     TEXT    NOT NULL,
  message     TEXT    NOT NULL,
  status      TEXT    NOT NULL DEFAULT 'new',
  ip          TEXT,
  user_agent  TEXT
);

CREATE INDEX IF NOT EXISTS idx_enquiries_created ON enquiries (created_at DESC);
