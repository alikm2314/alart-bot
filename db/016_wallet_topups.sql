CREATE TABLE IF NOT EXISTS wallet_topups (
  id bigserial PRIMARY KEY,
  user_id bigint NOT NULL REFERENCES users(id),
  amount_minor bigint NOT NULL CHECK (amount_minor > 0),
  approved_amount_minor bigint CHECK (approved_amount_minor > 0),
  request_key text NOT NULL,
  customer_reference text NOT NULL DEFAULT '',
  receipt_path text,
  status text NOT NULL DEFAULT 'awaiting_payment' CHECK (status IN ('awaiting_payment','manual_review','more_info','approved','rejected','cancelled')),
  reviewed_by bigint REFERENCES users(id),
  reviewed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(user_id,request_key)
);
CREATE INDEX IF NOT EXISTS wallet_topups_review ON wallet_topups(status,created_at DESC);
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number text;
