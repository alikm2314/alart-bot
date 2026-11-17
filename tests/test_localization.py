import unittest
from datetime import datetime, timezone, timedelta

from alart.localization import tr, localized_date
from alart.money import toman


class LocalizationTests(unittest.TestCase):
    def test_bot_and_notification_templates(self):
        self.assertEqual(tr("fa", "Your service has expired."), "اعتبار سرویس شما به پایان رسیده است.")
        self.assertEqual(tr("en", "Your service has expired."), "Your service has expired.")
        self.assertIn("۵", tr("fa", "Wallet balance: {amount}", amount="۵ تومان"))

    def test_dates_use_real_utc_conversion(self):
        local = datetime(2026, 9, 17, 14, 30, tzinfo=timezone(timedelta(hours=3, minutes=30)))
        self.assertEqual(localized_date("fa", local), "2026-09-17 11:00 UTC")

    def test_currency_label_follows_language(self):
        self.assertEqual(toman(500000, "en"), "50,000 Toman")
        self.assertEqual(toman(500000, "fa"), "50,000 تومان")


if __name__ == "__main__": unittest.main()
