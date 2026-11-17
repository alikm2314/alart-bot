import unittest
from unittest.mock import patch
from alart.provider import HTTPProvisioningProvider

class Response:
    def raise_for_status(self): pass
    def json(self): return {"id":"real-123","configuration":"config-from-provider"}
class Client:
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def post(self,url,headers,json=None):
        self.url=url
        self.headers=headers
        self.body=json
        return Response()

class ProvisioningTests(unittest.TestCase):
    @patch("alart.provider.httpx.Client")
    @patch("alart.provider._check_public_dns")
    def test_create_uses_provider_and_stable_key(self,dns_check,client_factory):
        client=Client()
        client_factory.return_value=client
        result=HTTPProvisioningProvider().create_service(key="order:7",plan={"id":4,"duration_hours":24},server={"api_url":"https://provider.test","api_token":"scoped-secret"},user_id=12)
        self.assertEqual(result["id"],"real-123")
        self.assertEqual(client.url,"https://provider.test/services")
        self.assertEqual(client.headers["Idempotency-Key"],"order:7")
        self.assertEqual(client.body["customer_id"],12)
        dns_check.assert_called_once_with("provider.test")

if __name__=="__main__": unittest.main()
