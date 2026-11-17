ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS subscription_token text UNIQUE;
CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_subscription_token_idx ON subscriptions(subscription_token) WHERE subscription_token IS NOT NULL;
