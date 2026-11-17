CREATE TABLE IF NOT EXISTS bot_payment_states (
  user_id bigint PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  stage text NOT NULL CHECK(stage IN ('topup_amount','topup_receipt','topup_photo','admin_adjust')),
  entity_id bigint,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
