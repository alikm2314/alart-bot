ALTER TABLE subscriptions ADD COLUMN archived_at timestamptz;
ALTER TABLE subscriptions ADD COLUMN remote_missing_at timestamptz;
ALTER TABLE subscriptions ADD COLUMN remote_checked_at timestamptz;
ALTER TABLE subscriptions ADD COLUMN links_checked_at timestamptz;
ALTER TABLE subscriptions ADD COLUMN ended_at timestamptz;
ALTER TABLE notifications ADD COLUMN event_key text;
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_action_path_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_action_path_check CHECK (
 action_path IS NULL OR action_path ~ '^/app/\?renew=[0-9]+$' OR action_path='/app/?page=notifications'
);
CREATE UNIQUE INDEX notifications_event_key ON notifications(event_key) WHERE event_key IS NOT NULL;
CREATE INDEX subscriptions_remote_check ON subscriptions(remote_checked_at) WHERE archived_at IS NULL;
-- URLs created before this release were guessed from the management endpoint.
-- Fetch them again from Sanaei's subscription-links API before displaying them.
UPDATE subscriptions SET provider_subscription_url=NULL;
