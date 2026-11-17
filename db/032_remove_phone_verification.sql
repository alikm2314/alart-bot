DROP TABLE IF EXISTS customer_controls;
ALTER TABLE users DROP COLUMN IF EXISTS phone_number;

ALTER TABLE broadcast_jobs DROP CONSTRAINT IF EXISTS broadcast_jobs_audience_check;
ALTER TABLE broadcast_jobs ADD CONSTRAINT broadcast_jobs_audience_check CHECK (audience IN
  ('all','purchased','no_purchase','trial_only','active_service','manual_payers','stars_payers','nowpayments_payers','wallet_payers'));
