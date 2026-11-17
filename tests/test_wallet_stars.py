import unittest
from alart.payment_integrations import TelegramStars

class WalletStarsTests(unittest.TestCase):
    def test_wallet_invoice_uses_topup_payload(self):
        calls=[]
        link,payload=TelegramStars(caller=lambda method,body:(calls.append((method,body)) or 'https://t.me/invoice')).wallet_invoice_link(topup_id=42,amount=7)
        self.assertEqual(link,'https://t.me/invoice')
        self.assertEqual(payload,'alart:topup:42')
        self.assertEqual(calls[0][0],'createInvoiceLink')
        self.assertEqual(calls[0][1]['currency'],'XTR')
        self.assertEqual(calls[0][1]['prices'][0]['amount'],7)
