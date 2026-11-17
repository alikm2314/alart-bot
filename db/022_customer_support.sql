CREATE TABLE IF NOT EXISTS support_settings (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  support_user_id bigint REFERENCES users(id),
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO support_settings(singleton) VALUES (true) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS support_requests (
  id bigserial PRIMARY KEY,
  user_id bigint NOT NULL REFERENCES users(id),
  body text NOT NULL CHECK (length(body) BETWEEN 1 AND 1500),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS support_requests_user_time_idx ON support_requests(user_id,created_at DESC);
