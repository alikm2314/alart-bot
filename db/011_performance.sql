CREATE INDEX IF NOT EXISTS payments_created_at_idx ON payments(created_at DESC);
CREATE INDEX IF NOT EXISTS payments_updated_at_idx ON payments(updated_at DESC) WHERE status='refunded';
