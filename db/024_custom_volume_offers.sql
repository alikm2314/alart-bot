ALTER TABLE services ADD COLUMN IF NOT EXISTS internal boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS custom_volume_offers (
  category text PRIMARY KEY CHECK (category IN ('volume_unlimited','volume_timed')),
  service_id bigint NOT NULL REFERENCES services(id),
  panel_id bigint NOT NULL REFERENCES managed_panels(id),
  rate_minor_per_gb bigint NOT NULL CHECK (rate_minor_per_gb>0),
  fixed_duration_days integer NOT NULL DEFAULT 0 CHECK (fixed_duration_days>=0),
  enabled boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now()
);
