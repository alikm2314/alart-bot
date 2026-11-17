import hashlib
import hmac
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from fastapi import HTTPException
from alart.auth import verify_telegram, require_operator

class TelegramAuthTests(unittest.TestCase):
    def signed(self, user='{"id":123,"first_name":"Ada"}', date=None):
        values={"auth_date":str(date or int(time.time())),"user":user}
        message="\n".join(f"{k}={v}" for k,v in sorted(values.items()))
        secret=hmac.new(b"WebAppData",b"test-token",hashlib.sha256).digest()
        values["hash"]=hmac.new(secret,message.encode(),hashlib.sha256).hexdigest()
        return urlencode(values)

    @patch("alart.auth.settings",return_value={"BOT_TOKEN":"test-token"})
    def test_valid_signature(self,_):
        self.assertEqual(verify_telegram(self.signed())["id"],123)

    @patch("alart.auth.settings",return_value={"BOT_TOKEN":"test-token"})
    def test_tampered_user_rejected(self,_):
        data=self.signed().replace("123","124")
        with self.assertRaises(HTTPException): verify_telegram(data)

    @patch("alart.auth.settings",return_value={"BOT_TOKEN":"test-token"})
    def test_expired_rejected(self,_):
        with self.assertRaises(HTTPException): verify_telegram(self.signed(date=int(time.time())-601))

    @patch("alart.auth.settings",return_value={"BOT_TOKEN":"test-token"})
    def test_duplicate_init_data_field_rejected(self,_):
        with self.assertRaises(HTTPException) as error:
            verify_telegram(self.signed()+'&user=%7B%22id%22%3A999%7D')
        self.assertEqual(error.exception.status_code,401)

    @patch("alart.auth.settings",return_value={"BOT_TOKEN":"test-token"})
    def test_signed_malformed_user_id_rejected(self,_):
        for value in ('{"id":"123"}','{"id":true}','{"id":0}'):
            with self.subTest(value=value),self.assertRaises(HTTPException):
                verify_telegram(self.signed(user=value))

    def test_viewer_cannot_perform_operator_actions(self):
        with patch("alart.auth.current_user",return_value={"id":1,"role":"viewer"}):
            with self.assertRaises(HTTPException) as error: require_operator(None)
        self.assertEqual(error.exception.status_code,403)

if __name__=="__main__": unittest.main()
