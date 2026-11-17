import unittest
from unittest.mock import patch

from alart import bot


class BotOnboardingTests(unittest.TestCase):
    def test_customers_use_inline_menu_and_admin_has_operational_keyboard_controls(self):
        regular = bot.keyboard("fa")
        menu = bot.main_menu("en")
        admin = bot.keyboard("en", admin=True)
        self.assertEqual(regular, {"remove_keyboard": True})
        self.assertEqual(menu["inline_keyboard"][0][0]["callback_data"], "menu:buy")
        self.assertEqual(admin["keyboard"], [[bot.LABELS["en"]["admin"], bot.LABELS["en"]["status"]],[bot.LABELS["en"]["topics"], bot.LABELS["en"]["home"]]])
        self.assertIn("secure", bot.main_menu_text("en"))
        self.assertIn("آلارت", bot.main_menu_text("fa"))

    def test_detail_menu_exposes_only_the_requested_back_control(self):
        self.assertEqual(bot.back_menu("en"),{"inline_keyboard":[[{"text":"‹ Back to main menu","callback_data":"menu:home"}]]})
        wallet=bot.back_menu("fa",wallet=True)["inline_keyboard"]
        self.assertEqual(wallet[0][0]["callback_data"],"menu:topup")
        self.assertEqual(wallet[1][0]["callback_data"],"menu:home")

    def test_onboarding_never_sends_home_before_terms(self):
        with patch.object(bot, "message") as send:
            bot.onboarding(123)
            bot.onboarding(123, "fa")
        self.assertEqual(send.call_count, 2)
        self.assertIn("Choose your preferred language",send.call_args_list[0].args[1])
        self.assertEqual(send.call_args_list[1].args[2]["inline_keyboard"][0][0]["callback_data"], "terms:1")

    def test_group_forward_supports_current_and_legacy_telegram_shapes(self):
        current={"forward_origin":{"type":"chat","sender_chat":{"id":-100123,"type":"supergroup"}}}
        legacy={"forward_from_chat":{"id":-100456,"type":"supergroup"}}
        self.assertEqual(bot.forwarded_group(current)["id"],-100123)
        self.assertEqual(bot.forwarded_group(legacy)["id"],-100456)

    def test_manual_transfer_uses_real_html_code_and_copy_button(self):
        text, markup=bot.manual_transfer_instruction(454545,"6219861458332540","en")
        self.assertIn("<code>6219861458332540</code>",text)
        self.assertNotIn("&lt;code&gt;",text)
        self.assertIn("receipt",text.lower())
        self.assertEqual(markup["inline_keyboard"][0][0]["copy_text"]["text"],"6219861458332540")

    def test_account_summary_escapes_profile_values_and_has_useful_details(self):
        profile={"id":1,"telegram_id":12345,"name":"A < B","username":"ali_test","balance_minor":125000,
                 "purchase_count":3,"active_service_count":2,"used_trial":True}
        text=bot.account_summary(profile,"en")
        self.assertIn("A &lt; B",text)
        self.assertIn("Telegram ID: <code>12345</code>",text)
        self.assertIn("Successful purchases: <b>3</b>",text)

    def test_chat_order_summary_uses_days_and_toman_for_a_paid_plan(self):
        order={"id":9,"price_minor":50,"selection":{"duration_hours":720,"traffic_gb":0,"users":1}}
        product={"description":"","trial_enabled":False,"price_minor":1750000,"discount_percent":0}
        text=bot.chat_order_summary(order,product,8946260013,"One-month plan","0 Toman","en")
        self.assertIn("Duration: 30 days",text)
        self.assertIn("Service price: 175,000 Toman",text)
        self.assertNotIn("Stars",text)
        self.assertNotIn("Duration: 720 hours",text)

    def test_chat_order_summary_uses_hours_only_for_a_trial(self):
        order={"id":1,"price_minor":0,"selection":{"duration_hours":24,"traffic_gb":1,"users":1}}
        product={"description":"","trial_enabled":True,"price_minor":0,"discount_percent":0}
        self.assertIn("Duration: 24 hours",bot.chat_order_summary(order,product,1,"Trial","0 Toman","en"))

    def test_chat_payment_details_shows_the_method_only_after_preinvoice(self):
        payment={"name":"One-month plan","catalog_price":1750000,"discount_percent":0,"price_minor":50}
        text=bot.chat_payment_details(payment,"en")
        self.assertIn("Telegram Stars",text)
        self.assertIn("175,000 Toman",text)
        self.assertIn("50 ⭐",text)

    def test_manual_order_instruction_has_copyable_destination(self):
        text, markup=bot.manual_order_instruction(9,1750000,"6219861458332540","en")
        self.assertIn("175,000 Toman",text)
        self.assertIn("<code>6219861458332540</code>",text)
        self.assertEqual(markup['inline_keyboard'][0][0]['copy_text']['text'],"6219861458332540")



if __name__ == "__main__":
    unittest.main()
