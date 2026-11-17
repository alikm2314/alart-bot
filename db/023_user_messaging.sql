ALTER TABLE users ADD COLUMN IF NOT EXISTS photo_url text;

CREATE TABLE IF NOT EXISTS broadcast_jobs (
  id bigserial PRIMARY KEY,
  audience text NOT NULL CHECK (audience IN ('all','purchased','no_purchase','trial_only','active_service')),
  body text NOT NULL CHECK (length(body) BETWEEN 1 AND 1500),
  created_by bigint NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);
CREATE TABLE IF NOT EXISTS broadcast_recipients (
  job_id bigint NOT NULL REFERENCES broadcast_jobs(id) ON DELETE CASCADE,
  user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed')),
  sent_at timestamptz,
  last_error text,
  PRIMARY KEY (job_id,user_id)
);
CREATE INDEX IF NOT EXISTS broadcast_recipients_pending_idx ON broadcast_recipients(job_id,status,user_id);
