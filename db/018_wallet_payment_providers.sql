ALTER TABLE wallet_topups ADD COLUMN IF NOT EXISTS provider text NOT NULL DEFAULT 'manual' CHECK (provider IN ('manual','stars','nowpayments'));
ALTER TABLE wallet_topups ADD COLUMN IF NOT EXISTS currency text NOT NULL DEFAULT 'IRR';
ALTER TABLE wallet_topups ADD COLUMN IF NOT EXISTS invoice_payload text UNIQUE;
ALTER TABLE wallet_topups ADD COLUMN IF NOT EXISTS provider_reference text UNIQUE;
ALTER TABLE bot_payment_states ADD COLUMN IF NOT EXISTS provider text;
ALTER TABLE bot_payment_states DROP CONSTRAINT IF EXISTS bot_payment_states_stage_check;
ALTER TABLE bot_payment_states ADD CONSTRAINT bot_payment_states_stage_check CHECK(stage IN ('topup_amount','topup_receipt','topup_photo','admin_adjust'));
ALTER TABLE wallet_topups ADD COLUMN IF NOT EXISTS amount_minor_irr bigint; UPDATE wallet_topups SET amount_minor_irr=amount_minor WHERE amount_minor_irr IS NULL; ALTER TABLE wallet_topups ALTER COLUMN amount_minor_irr SET NOT NULL;
