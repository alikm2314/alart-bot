-- Additive lifecycle records; original payments and ledger entries are retained.
ALTER TABLE orders DROP CONSTRAINT orders_status_check;
ALTER TABLE orders ADD CONSTRAINT orders_status_check CHECK (status IN ('awaiting_payment','manual_review','paid','provisioning','active','failed','refunded','rejected','expired','cancelled'));
CREATE TABLE refund_requests (
  id bigserial PRIMARY KEY,
  order_id bigint NOT NULL UNIQUE REFERENCES orders(id),
  requested_by bigint REFERENCES users(id),
  reviewed_by bigint REFERENCES users(id),
  reason text NOT NULL,
  status text NOT NULL DEFAULT 'requested' CHECK (status IN ('requested','approved','refund_pending','processing','manual_review','completed','rejected')),
  remote_disabled_at timestamptz,
  confirmed_at timestamptz,
  confirmation_source text CHECK (confirmation_source IN ('wallet','telegram','nowpayments','operator','none')),
  confirmation_reference text,
  last_error text,
  attempts integer NOT NULL DEFAULT 0,
  run_after timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX refund_requests_queue ON refund_requests(run_after,id) WHERE status IN ('approved','refund_pending','processing');
CREATE UNIQUE INDEX refund_confirmation_unique ON refund_requests(confirmation_source,confirmation_reference) WHERE confirmation_reference IS NOT NULL;
