CREATE TABLE referral_settings (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  enabled boolean NOT NULL DEFAULT true,
  referrer_reward_minor bigint NOT NULL DEFAULT 500000 CHECK (referrer_reward_minor>=0),
  referred_reward_minor bigint NOT NULL DEFAULT 300000 CHECK (referred_reward_minor>=0),
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO referral_settings(singleton) VALUES (true) ON CONFLICT DO NOTHING;

CREATE TABLE referral_codes (
  user_id bigint PRIMARY KEY REFERENCES users(id),
  code varchar(20) NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE referrals (
  referred_user_id bigint PRIMARY KEY REFERENCES users(id),
  referrer_user_id bigint NOT NULL REFERENCES users(id),
  qualified_order_id bigint UNIQUE REFERENCES orders(id),
  status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','awarded','reversal_pending','reversed')),
  referred_at timestamptz NOT NULL DEFAULT now(),
  qualified_at timestamptz,
  CHECK (referred_user_id<>referrer_user_id)
);
CREATE INDEX referrals_referrer_idx ON referrals(referrer_user_id,referred_at DESC);
CREATE TABLE referral_rewards (
  id bigserial PRIMARY KEY,
  referred_user_id bigint NOT NULL REFERENCES referrals(referred_user_id),
  beneficiary_user_id bigint NOT NULL REFERENCES users(id),
  role text NOT NULL CHECK(role IN ('referrer','referred')),
  amount_minor bigint NOT NULL CHECK(amount_minor>0),
  status text NOT NULL DEFAULT 'awarded' CHECK(status IN ('awarded','reversal_pending','reversed')),
  created_at timestamptz NOT NULL DEFAULT now(),
  reversed_at timestamptz,
  UNIQUE(referred_user_id,role)
);
CREATE INDEX referral_rewards_pending_idx ON referral_rewards(beneficiary_user_id) WHERE status='reversal_pending';

ALTER TABLE wallet_transactions DROP CONSTRAINT wallet_transactions_kind_check;
ALTER TABLE wallet_transactions ADD CONSTRAINT wallet_transactions_kind_check
  CHECK(kind IN ('deposit','purchase','refund','adjustment','referral_reward','referral_reversal'));
