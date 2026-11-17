ALTER TABLE users ADD COLUMN IF NOT EXISTS language text CHECK (language IN ('fa','en'));
ALTER TABLE users ADD COLUMN IF NOT EXISTS accepted_terms_version text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS accepted_terms_at timestamptz;
