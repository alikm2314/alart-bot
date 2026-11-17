-- Persist an absolute renewal target so retries cannot grant another period.
ALTER TABLE renewal_jobs ADD COLUMN prepared_at timestamptz;
ALTER TABLE renewal_jobs ADD COLUMN previous_expires_at timestamptz;
ALTER TABLE renewal_jobs ADD COLUMN target_expires_at timestamptz;
ALTER TABLE renewal_jobs ADD COLUMN previous_traffic_limit_bytes bigint;
ALTER TABLE renewal_jobs ADD COLUMN target_traffic_limit_bytes bigint;
ALTER TABLE renewal_jobs ADD COLUMN added_traffic_bytes bigint NOT NULL DEFAULT 0 CHECK (added_traffic_bytes>=0);
ALTER TABLE renewal_jobs ADD COLUMN duration_hours integer NOT NULL DEFAULT 0 CHECK (duration_hours>=0);
ALTER TABLE renewal_jobs ADD COLUMN renewal_mode text CHECK (renewal_mode IN ('time','time_and_traffic','traffic_topup'));
ALTER TABLE subscriptions ADD COLUMN renewal_reminded_for timestamptz;
ALTER TABLE notifications ADD COLUMN action_path text CHECK (action_path IS NULL OR action_path ~ '^/app/\?renew=[0-9]+$');
ALTER TABLE refund_requests ADD COLUMN service_action text NOT NULL DEFAULT 'cancel' CHECK (service_action IN ('cancel','refund_only'));
CREATE INDEX IF NOT EXISTS renewal_jobs_subscription_idx ON renewal_jobs(subscription_id,id DESC);
