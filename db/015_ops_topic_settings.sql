CREATE TABLE IF NOT EXISTS ops_topic_settings (
  category text PRIMARY KEY,
  chat_id bigint NOT NULL,
  topic_id bigint NOT NULL CHECK(topic_id>0),
  created_at timestamptz NOT NULL DEFAULT now()
);
