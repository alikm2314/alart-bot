ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS traffic_used_bytes bigint CHECK (traffic_used_bytes IS NULL OR traffic_used_bytes>=0);
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS traffic_sampled_at timestamptz;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS traffic_check_at timestamptz;
CREATE INDEX subscriptions_traffic_due ON subscriptions(traffic_check_at NULLS FIRST)
  WHERE status IN ('active','expiring') AND traffic_limit_bytes IS NOT NULL;
