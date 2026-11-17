import unittest
from pydantic import ValidationError
from alart.api import Buy, Quote, Ask, SupportRequest, AdminMessage, BroadcastMessage, MessageButton, app

class RequestValidationTests(unittest.TestCase):
    def test_panel_stylesheet_has_a_public_route(self):
        self.assertIn('/panel/operations.css',{route.path for route in app.routes if hasattr(route,'path')})

    def test_negative_expected_price_rejected(self):
        with self.assertRaises(ValidationError):
            Buy(plan_id=1,location_id=1,request_key="abcdefghijkl",expected_final_minor=-1)

    def test_long_question_rejected(self):
        with self.assertRaises(ValidationError):
            Ask(question="x"*1001)

    def test_support_message_is_bounded(self):
        self.assertEqual(SupportRequest(body="  Need help ").body,"  Need help ")
        with self.assertRaises(ValidationError):
            SupportRequest(body="x"*1501)

    def test_miniapp_experience_stylesheet_has_a_public_route(self):
        self.assertIn('/app/experience.css',{route.path for route in app.routes if hasattr(route,'path')})

    def test_customer_messaging_inputs_are_bounded_and_audience_is_fixed(self):
        self.assertEqual(BroadcastMessage(audience='manual_payers',body='New route',buttons=[MessageButton(label='Buy now',surface='chat',action='buy',coupon_code='SAVE')]).audience,'manual_payers')
        with self.assertRaises(ValidationError):
            AdminMessage(body='x'*1501)
        with self.assertRaises(ValidationError):
            BroadcastMessage(audience='arbitrary_sql',body='no')
        with self.assertRaises(ValidationError):
            MessageButton(label='Bad',action='https://unsafe.example')

    def test_quote_input(self):
        quote=Quote(plan_id=7,coupon_code="SAVE")
        self.assertEqual(quote.plan_id,7)

if __name__=="__main__": unittest.main()
