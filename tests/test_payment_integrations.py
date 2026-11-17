import hashlib
import hmac
import json
import unittest
from unittest.mock import patch
from alart.payment_integrations import TelegramStars,NOWPayments,stars_precheckout_matches,verify_nowpayments_ipn

class PaymentIntegrationTests(unittest.TestCase):
    def test_stars_invoice_and_precheckout(self):
        calls=[]
        provider=TelegramStars(lambda method,payload:(calls.append((method,payload)) or "https://t.me/invoice" if method=="createInvoiceLink" else True))
        url,payload=provider.invoice_link(order_id=7,title="Unlimited",amount=25)
        self.assertEqual(url,"https://t.me/invoice")
        self.assertEqual(calls[0][1]["currency"],"XTR")
        self.assertEqual(calls[0][1]["prices"],[{"label":"Unlimited","amount":25}])
        row={"invoice_payload":payload,"amount_minor":25}
        query={"invoice_payload":payload,"currency":"XTR","total_amount":25,"from":{"id":123}}
        self.assertTrue(stars_precheckout_matches(query,row,123,"awaiting_payment"))
        self.assertFalse(stars_precheckout_matches(query,row,123,"paid"))
        self.assertFalse(stars_precheckout_matches({**query,"total_amount":24},row,123,"awaiting_payment"))

    def test_nowpayments_signature_and_tamper(self):
        payload={"payment_id":55,"payment_status":"finished","price_amount":10.0,"nested":{"b":2,"a":1}}
        canonical=json.dumps({"nested":{"a":1,"b":2},"payment_id":55,"payment_status":"finished","price_amount":10.0},separators=(",",":"))
        signature=hmac.new(b"secret",canonical.encode(),hashlib.sha512).hexdigest()
        self.assertTrue(verify_nowpayments_ipn(payload,signature,"secret"))
        self.assertFalse(verify_nowpayments_ipn({**payload,"price_amount":9.0},signature,"secret"))

    @patch("alart.payment_integrations.now_credentials",return_value=("api-key","ipn-secret"))
    def test_nowpayments_create_uses_actual_api(self,_):
        class Client:
            def request(self,method,url,**kwargs):
                self.method=method;self.url=url;self.body=kwargs["json"]
                class Response:
                    def raise_for_status(self): pass
                    def json(self): return {"payment_id":123,"pay_address":"address","payment_status":"waiting"}
                return Response()
        client=Client()
        result=NOWPayments(client).create_payment(order_id=8,usd_cents=1250,pay_currency="usdttrc20",callback_url="https://alart.test/api/ipn")
        self.assertEqual(result["payment_id"],123)
        self.assertEqual(client.url,"https://api.nowpayments.io/v1/payment")
        self.assertEqual(client.body["price_amount"],12.5)
        self.assertEqual(client.body["order_id"],"alart:8")

if __name__=="__main__": unittest.main()
