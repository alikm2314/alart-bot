CREATE TABLE admin_login_codes (code_hash text PRIMARY KEY, user_id bigint NOT NULL REFERENCES users(id), expires_at timestamptz NOT NULL, used_at timestamptz);
