CREATE TABLE finance_entries (
  id bigserial PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('expense','fee','manual_sale')),
  amount_minor bigint NOT NULL CHECK (amount_minor>0),
  currency text NOT NULL DEFAULT 'IRR' CHECK (currency='IRR'),
  category text NOT NULL DEFAULT 'other' CHECK (category IN ('server','domain','api','advertising','other')),
  description text NOT NULL CHECK (length(description) BETWEEN 3 AND 500),
  reference text,
  actor_id bigint NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  reversed_by bigint REFERENCES users(id),
  reversed_at timestamptz
);
CREATE INDEX finance_entries_created_idx ON finance_entries(created_at DESC);
CREATE UNIQUE INDEX finance_entries_reference_idx ON finance_entries(reference) WHERE reference IS NOT NULL;
