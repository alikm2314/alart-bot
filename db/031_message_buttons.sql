-- Store operator-selected Mini App actions with queued broadcasts.
ALTER TABLE broadcast_jobs
  ADD COLUMN IF NOT EXISTS buttons jsonb NOT NULL DEFAULT '[]'::jsonb;
