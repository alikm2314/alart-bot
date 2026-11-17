CREATE TABLE ops_events (
  id bigserial PRIMARY KEY,
  event_type text NOT NULL,
  entity_id bigint NOT NULL,
  event_key text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  delivered_at timestamptz,
  attempts integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ops_events_pending_idx ON ops_events(next_attempt_at,id) WHERE delivered_at IS NULL;
