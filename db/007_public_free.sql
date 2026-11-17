ALTER TABLE plans ADD COLUMN IF NOT EXISTS free_purchase_enabled boolean NOT NULL DEFAULT false;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS is_public_free boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX IF NOT EXISTS orders_public_free_once ON orders(user_id,plan_id) WHERE is_public_free;
