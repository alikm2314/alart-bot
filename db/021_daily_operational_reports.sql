CREATE TABLE IF NOT EXISTS daily_operational_reports (
  report_date date PRIMARY KEY,
  delivered_at timestamptz NOT NULL DEFAULT now()
);
