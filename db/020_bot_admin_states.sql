CREATE TABLE IF NOT EXISTS bot_admin_states (
  user_id bigint PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  stage text NOT NULL CHECK(stage IN ('topics_wait_group')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
