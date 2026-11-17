CREATE TABLE connection_guides (
 platform text NOT NULL CHECK(platform IN ('ios','android','windows','macos')),
 language text NOT NULL CHECK(language IN ('fa','en')),
 title text NOT NULL, body text NOT NULL, video_key text,
 published boolean NOT NULL DEFAULT true, updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(platform,language)
);
INSERT INTO connection_guides(platform,language,title,body)
SELECT platform,language,
 CASE WHEN language='fa' THEN 'آموزش اتصال' ELSE 'Connection guide' END,
 CASE WHEN language='fa' THEN '۱. یک برنامهٔ سازگار با اشتراک خود روی دستگاه نصب کنید.
۲. دکمهٔ «کپی لینک اشتراک» را بزنید.
۳. در برنامه، افزودن اشتراک یا Import from URL را انتخاب و لینک را وارد کنید. برای اسکن QR می‌توانید از دوربین دستگاه دیگری استفاده کنید.
۴. اشتراک را به‌روز کنید، یک سرور انتخاب کنید و اتصال را روشن کنید.
اگر اتصال برقرار نشد، ابتدا وضعیت سرویس و حجم باقی‌مانده را بررسی کنید؛ سپس با پشتیبانی تماس بگیرید.'
 ELSE '1. Install a client compatible with your subscription on your device.
2. Tap Copy subscription link.
3. In the client, choose Add subscription or Import from URL and paste the link. You can scan the QR using another device.
4. Refresh the subscription, select a server and connect.
If the connection fails, check your service status and remaining allowance, then contact support.' END
FROM unnest(ARRAY['ios','android','windows','macos']) AS platform
CROSS JOIN unnest(ARRAY['fa','en']) AS language;
