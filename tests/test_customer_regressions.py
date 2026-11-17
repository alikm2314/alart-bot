import io
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import httpx
from PIL import Image
from alart.sanaei import SanaeiProvider, PanelError
from alart.qr import provider_subscription_png
from alart.service_state import effective_status, present
from alart.lifecycle import notice_for
from alart.service_delivery import service_message

class CustomerRegressions(unittest.TestCase):
    def test_subscription_uses_panel_public_uri_not_management_path_or_uuid(self):
        calls=[]
        def handle(request):
            calls.append(request.url.path)
            obj=['vless://test@server.test:443'] if '/clients/links/' in request.url.path else {'subEnable':True,'subURI':'https://public.test:2096/custom-path/'}
            return httpx.Response(200,json={'success':True,'obj':obj})
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            p=SanaeiProvider('https://manager.test:2053/private','test',client=client)
            result=p._client_result('qa',{'subId':'subscription-id','uuid':'protocol-id'})
        self.assertEqual(result['subscription_url'],'https://public.test:2096/custom-path/subscription-id')
        self.assertIn('/private/panel/api/setting/defaultSettings',calls)
        self.assertEqual(result['configuration'],'vless://test@server.test:443')

    def test_protocol_uuid_is_never_used_as_subscription_id(self):
        with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'success':True,'obj':['vless://test@host']}))) as client:
            p=SanaeiProvider('https://manager.test','test',client=client)
            self.assertIsNone(p._client_result('qa',{'uuid':'protocol-id'})['subscription_url'])

    def test_expired_view_hides_connection_even_when_worker_disable_fails(self):
        sub={'status':'active','expires_at':datetime.now(timezone.utc)-timedelta(seconds=1),'configuration':'secret','provider_subscription_url':'https://secret.test'}
        self.assertEqual(effective_status(sub),'expired')
        self.assertIsNone(present(sub)['configuration'])
        self.assertIsNone(present(sub)['provider_subscription_url'])
        self.assertEqual(sub['status'],'active') # presentation does not defeat remote retry

    def test_volume_depletion_and_confirmed_deletion_are_expired(self):
        for sub in ({'traffic_limit_bytes':100,'traffic_used_bytes':100},{'remote_missing_at':datetime.now(timezone.utc)}):
            self.assertEqual(effective_status({'status':'active',**sub}),'expired')

    def test_reminders_have_identity_localized_remaining_time_and_stable_key(self):
        now=datetime.now(timezone.utc)
        row={'id':8,'language':'fa','status':'active','expires_at':now+timedelta(hours=3)}
        key,body=notice_for(row,now)
        self.assertIn('#8',body);self.assertIn('3 ساعت',body)
        self.assertEqual(key,notice_for(row,now+timedelta(minutes=1))[0])

    def test_expired_bot_message_does_not_show_stale_allowance(self):
        sub={'id':1,'name':'<test>','status':'expired','traffic_limit_bytes':1024**3,'traffic_used_bytes':100,'price_minor':100}
        with patch('alart.service_delivery.settings',return_value={'DOMAIN':'test.example'}):
            text,markup=service_message(sub,'fa')
        self.assertIn('منقضی',text);self.assertNotIn('GB',text);self.assertIn('&lt;test&gt;',text)
        self.assertTrue(any('تمدید' in b['text'] for row in markup['inline_keyboard'] for b in row))

    def test_qr_quiet_zone_and_long_url_do_not_crop(self):
        for url in ('https://public.test:2096/sub/abc','https://public.test/sub/'+'a'*1700):
            png=provider_subscription_png(url)
            im=Image.open(io.BytesIO(png)).convert('RGB')
            self.assertEqual(im.width,im.height)
            self.assertEqual(im.getpixel((0,0)),(255,255,255))
            self.assertLessEqual(im.width,900)
            try: import zxingcpp
            except ImportError: continue
            decoded=zxingcpp.read_barcode(im)
            self.assertIsNotNone(decoded)
            self.assertEqual(decoded.text,url)

if __name__=='__main__':unittest.main()
