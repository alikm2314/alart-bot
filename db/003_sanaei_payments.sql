ALTER TABLE users ADD COLUMN IF NOT EXISTS username text;
ALTER TABLE servers ADD COLUMN IF NOT EXISTS panel_id bigint;
CREATE TABLE managed_panels (
  id bigserial PRIMARY KEY,
  name text NOT NULL,
  base_url text NOT NULL,
  credential_ciphertext text NOT NULL,
  provider text NOT NULL DEFAULT 'sanaei' CHECK (provider='sanaei'),
  enabled boolean NOT NULL DEFAULT false,
  health text NOT NULL DEFAULT 'unavailable' CHECK (health IN ('online','offline','authentication_failed','unavailable','disabled')),
  latency_ms integer,
  last_ok_at timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE servers ADD CONSTRAINT servers_panel_id_fkey FOREIGN KEY (panel_id) REFERENCES managed_panels(id);
CREATE UNIQUE INDEX servers_panel_unique ON servers(panel_id) WHERE panel_id IS NOT NULL;
CREATE TABLE panel_inbounds (
  id bigserial PRIMARY KEY,
  panel_id bigint NOT NULL REFERENCES managed_panels(id) ON DELETE CASCADE,
  remote_id integer NOT NULL CHECK (remote_id>0),
  remark text NOT NULL,
  protocol text NOT NULL,
  remote_enabled boolean NOT NULL,
  allowed boolean NOT NULL DEFAULT false,
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (panel_id,remote_id)
);
CREATE TABLE service_inbound_mappings (
  service_id bigint NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  inbound_id bigint NOT NULL REFERENCES panel_inbounds(id) ON DELETE CASCADE,
  PRIMARY KEY (service_id,inbound_id)
);
ALTER TABLE services ADD COLUMN IF NOT EXISTS category text NOT NULL DEFAULT 'legacy' CHECK (category IN ('legacy','unlimited','volume_unlimited','volume_timed'));
ALTER TABLE plans DROP CONSTRAINT IF EXISTS plans_duration_hours_check;
ALTER TABLE plans ADD CONSTRAINT plans_duration_hours_check CHECK (duration_hours>=0);
ALTER TABLE plans ADD COLUMN IF NOT EXISTS traffic_gb integer CHECK (traffic_gb IS NULL OR traffic_gb>0);
ALTER TABLE plans ADD COLUMN IF NOT EXISTS user_limit integer NOT NULL DEFAULT 1 CHECK (user_limit>0);
ALTER TABLE plans ADD COLUMN IF NOT EXISTS stars_price integer CHECK (stars_price IS NULL OR stars_price>0);
ALTER TABLE plans ADD COLUMN IF NOT EXISTS usd_price_cents integer CHECK (usd_price_cents IS NULL OR usd_price_cents>0);
ALTER TABLE plans ADD COLUMN IF NOT EXISTS is_custom boolean NOT NULL DEFAULT false;
CREATE TABLE pricing_rules (
  service_id bigint PRIMARY KEY REFERENCES services(id) ON DELETE CASCADE,
  base_minor bigint NOT NULL CHECK (base_minor>=0),
  per_user_minor bigint NOT NULL DEFAULT 0 CHECK (per_user_minor>=0),
  per_gb_minor bigint NOT NULL DEFAULT 0 CHECK (per_gb_minor>=0),
  per_day_minor bigint NOT NULL DEFAULT 0 CHECK (per_day_minor>=0),
  base_stars integer CHECK (base_stars IS NULL OR base_stars>0),
  per_user_stars integer NOT NULL DEFAULT 0 CHECK (per_user_stars>=0),
  per_gb_stars integer NOT NULL DEFAULT 0 CHECK (per_gb_stars>=0),
  per_day_stars integer NOT NULL DEFAULT 0 CHECK (per_day_stars>=0),
  base_usd_cents integer CHECK (base_usd_cents IS NULL OR base_usd_cents>0),
  per_user_usd_cents integer NOT NULL DEFAULT 0 CHECK (per_user_usd_cents>=0),
  per_gb_usd_cents integer NOT NULL DEFAULT 0 CHECK (per_gb_usd_cents>=0),
  per_day_usd_cents integer NOT NULL DEFAULT 0 CHECK (per_day_usd_cents>=0),
  min_users integer NOT NULL DEFAULT 1 CHECK (min_users>0),
  max_users integer NOT NULL DEFAULT 3 CHECK (max_users>=min_users),
  min_traffic_gb integer NOT NULL DEFAULT 1 CHECK (min_traffic_gb>0),
  max_traffic_gb integer NOT NULL DEFAULT 500 CHECK (max_traffic_gb>=min_traffic_gb),
  min_days integer NOT NULL DEFAULT 1 CHECK (min_days>0),
  max_days integer NOT NULL DEFAULT 365 CHECK (max_days>=min_days),
  enabled boolean NOT NULL DEFAULT false
);
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check CHECK (status IN ('awaiting_payment','manual_review','paid','provisioning','active','failed','refunded','rejected','expired'));
ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_method text NOT NULL DEFAULT 'wallet' CHECK (payment_method IN ('wallet','stars','nowpayments','manual'));
ALTER TABLE orders ADD COLUMN IF NOT EXISTS selection jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS currency text NOT NULL DEFAULT 'IRR';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS renewal_of_sub_id bigint REFERENCES subscriptions(id);
ALTER TABLE payments DROP CONSTRAINT IF EXISTS payments_status_check;
ALTER TABLE payments ADD CONSTRAINT payments_status_check CHECK (status IN ('pending','waiting','confirming','confirmed','finished','failed','expired','refunded','manual_review','rejected'));
ALTER TABLE payments ADD COLUMN IF NOT EXISTS currency text NOT NULL DEFAULT 'IRR';
ALTER TABLE payments ADD COLUMN IF NOT EXISTS invoice_payload text UNIQUE;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS provider_data jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX payments_order_idx ON payments(order_id);
CREATE TABLE payment_events (
  id bigserial PRIMARY KEY,
  payment_id bigint NOT NULL REFERENCES payments(id),
  event_key text NOT NULL UNIQUE,
  provider_status text NOT NULL,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  received_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE manual_payment_submissions (
  payment_id bigint PRIMARY KEY REFERENCES payments(id),
  customer_reference text NOT NULL,
  receipt_path text,
  review_status text NOT NULL DEFAULT 'manual_review' CHECK (review_status IN ('manual_review','approved','rejected','more_info')),
  reviewed_by bigint REFERENCES users(id),
  reviewed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE payment_settings (
  provider text PRIMARY KEY CHECK (provider IN ('stars','nowpayments','manual')),
  enabled boolean NOT NULL DEFAULT false,
  public_details text NOT NULL DEFAULT '',
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  credential_ciphertext text,
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO payment_settings(provider) VALUES ('stars'),('nowpayments'),('manual') ON CONFLICT DO NOTHING;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS traffic_limit_bytes bigint CHECK (traffic_limit_bytes IS NULL OR traffic_limit_bytes>0);
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS user_limit integer CHECK (user_limit IS NULL OR user_limit>0);
CREATE TABLE provisioned_configurations (
  id bigserial PRIMARY KEY,
  user_id bigint NOT NULL REFERENCES users(id),
  subscription_id bigint NOT NULL UNIQUE REFERENCES subscriptions(id),
  order_id bigint NOT NULL UNIQUE REFERENCES orders(id),
  panel_id bigint NOT NULL REFERENCES managed_panels(id),
  client_email text NOT NULL,
  client_name text NOT NULL,
  inbound_ids integer[] NOT NULL,
  remote_client_id text,
  traffic_limit_bytes bigint,
  user_limit integer NOT NULL,
  expires_at timestamptz,
  status text NOT NULL CHECK (status IN ('pending','active','expired','disabled')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(panel_id,client_email)
);
CREATE INDEX configurations_user_idx ON provisioned_configurations(user_id,created_at DESC);
CREATE TABLE user_configuration_counters (
  user_id bigint PRIMARY KEY REFERENCES users(id),
  last_number integer NOT NULL CHECK (last_number>0)
);
CREATE TABLE renewal_jobs (
  id bigserial PRIMARY KEY,
  order_id bigint NOT NULL UNIQUE REFERENCES orders(id),
  subscription_id bigint NOT NULL REFERENCES subscriptions(id),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','retrying','succeeded','failed')),
  attempts integer NOT NULL DEFAULT 0,
  run_after timestamptz NOT NULL DEFAULT now(),
  lease_until timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX renewal_jobs_queue ON renewal_jobs(run_after) WHERE status IN ('pending','processing','retrying');
CREATE TABLE customer_login_codes (
  code_hash text PRIMARY KEY,
  user_id bigint NOT NULL REFERENCES users(id),
  expires_at timestamptz NOT NULL,
  used_at timestamptz
);
