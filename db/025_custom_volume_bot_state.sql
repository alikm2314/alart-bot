ALTER TABLE bot_payment_states DROP CONSTRAINT IF EXISTS bot_payment_states_stage_check;
ALTER TABLE bot_payment_states ADD CONSTRAINT bot_payment_states_stage_check CHECK(stage IN ('topup_amount','topup_receipt','topup_photo','admin_adjust','custom_volume_amount'));
