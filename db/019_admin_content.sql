CREATE TABLE IF NOT EXISTS bot_content (
  content_key text PRIMARY KEY,
  fa text NOT NULL,
  en text NOT NULL,
  updated_by bigint REFERENCES users(id),
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO bot_content(content_key,fa,en) VALUES
('terms','شرایط استفاده نسخه ۱: از این سرویس مطابق قوانین استفاده کنید. اطلاعات اتصال خود را محرمانه نگه دارید. خرید و بازپرداخت طبق شرایط اعلام‌شده در زمان سفارش انجام می‌شود. برای دریافت خدمات، پذیرش این شرایط لازم است.','Terms v1: Use this service lawfully and keep your connection details private. Purchases and refunds follow the terms shown at checkout. You must accept these terms to use Alart.')
ON CONFLICT (content_key) DO NOTHING;
