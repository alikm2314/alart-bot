-- Preserve the order from the source panel. A successful discovery is an
-- authoritative snapshot; stale rows are removed by panel_management.py.
ALTER TABLE panel_inbounds
  ADD COLUMN IF NOT EXISTS remote_order integer NOT NULL DEFAULT 0 CHECK (remote_order >= 0);

CREATE INDEX IF NOT EXISTS panel_inbounds_panel_order_idx
  ON panel_inbounds(panel_id, remote_order, remote_id);
