import json
import unittest
from unittest.mock import patch
import httpx
from cryptography.fernet import Fernet
from alart.sanaei import SanaeiProvider, PanelAuthenticationError, PanelError, client_name, encrypt_token, decrypt_token, validate_panel_url, _check_public_dns

class SanaeiTests(unittest.TestCase):
    def test_server_and_node_metrics_use_only_reported_values(self):
        def handle(request):
            if request.url.path.endswith('server/status'):
                return httpx.Response(200,json={'success':True,'obj':{'cpu':22.5,'mem':{'current':512,'total':1024},'swap':{'current':0,'total':0},'disk':{'current':20,'total':100},'uptime':3600}})
            if request.url.path.endswith('nodes/list'):
                return httpx.Response(200,json={'success':True,'obj':[{'id':7,'name':'Node 1','status':'online','cpuPct':30,'memPct':45,'uptimeSecs':90,'onlineCount':4},{'id':8,'cpuPct':900}]})
            return httpx.Response(404)
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            provider=SanaeiProvider('https://panel.test','token',client=client)
            server=provider.server_status()
            nodes=provider.nodes_status()
        self.assertEqual(server['cpu_percent'],22.5)
        self.assertEqual(server['ram_percent'],50)
        self.assertIsNone(server['swap_percent'])
        self.assertEqual(server['disk_percent'],20)
        self.assertEqual(nodes[0]['online_count'],4)
        self.assertIsNone(nodes[1]['cpu_percent'])

    def test_client_list_reads_documented_nested_traffic_without_inventing_online_state(self):
        response={'success':True,'obj':[{'email':'alice','group':'trial','totalGB':0,'enable':True,'traffic':{'up':1024,'down':2048}}]}
        with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=response))) as client:
            row=SanaeiProvider('https://panel.test','token',client=client).list_clients()[0]
        self.assertEqual((row['up_bytes'],row['down_bytes'],row['total_bytes']),(1024,2048,0))
        self.assertEqual(row['group'],'trial')
        self.assertTrue(row['enabled'])
        self.assertIsNone(row['online'])

    def test_names_include_immutable_id(self):
        self.assertEqual(client_name("ali1234",123,42,1),"ali1234-u42-001")
        self.assertEqual(client_name(None,123,42,2),"user-123-u42-002")

    def test_url_rejects_http_and_embedded_password(self):
        with self.assertRaises(ValueError): validate_panel_url("http://panel.test")
        with self.assertRaises(ValueError): validate_panel_url("https://user:secret@panel.test")
        for unsafe in ("https://127.0.0.1","https://169.254.169.254","https://localhost","https://host.local"):
            with self.assertRaises(ValueError): validate_panel_url(unsafe)

    def test_dns_rejects_private_result(self):
        with patch("alart.sanaei.socket.getaddrinfo",return_value=[(2,1,0,"",("127.0.0.1",443))]):
            with self.assertRaises(PanelError): _check_public_dns("public.example")

    def test_credential_round_trip(self):
        key=Fernet.generate_key().decode()
        with patch("alart.sanaei.settings",return_value={"PANEL_ENCRYPTION_KEY":key}):
            encrypted=encrypt_token("real-secret")
            self.assertNotIn("real-secret",encrypted)
            self.assertEqual(decrypt_token(encrypted),"real-secret")

    def test_discovery_and_create_idempotency(self):
        calls=[]
        created={"value":False}
        def handle(request):
            self.assertEqual(request.headers["authorization"],"Bearer scoped-token")
            path=request.url.path
            calls.append(path)
            if path.endswith("inbounds/options"):
                return httpx.Response(200,json={"success":True,"obj":[{"id":3,"remark":"VLESS","protocol":"vless","enable":True}]})
            if path.endswith("clients/get/test-u7-001"):
                return httpx.Response(200,json={"success":True,"obj":{"email":"test-u7-001","tgId":99,"id":"remote-1"} if created["value"] else None})
            if path.endswith("clients/add"):
                body=json.loads(request.content)
                self.assertEqual(body["inboundIds"],[3])
                self.assertEqual(body["client"]["totalGB"],50*1024**3)
                created["value"]=True
                return httpx.Response(200,json={"success":True})
            if path.endswith("clients/links/test-u7-001"):
                return httpx.Response(200,json={"success":True,"obj":["vless://credential@host:443"]})
            return httpx.Response(404)
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            provider=SanaeiProvider("https://panel.test","scoped-token",client=client)
            self.assertEqual(provider.discover_inbounds()[0]["remote_id"],3)
            arguments={"email":"test-u7-001","telegram_id":99,"inbound_ids":[3],"traffic_bytes":50*1024**3,"expires_at_ms":None,"user_limit":1}
            first=provider.create_client(**arguments)
            second=provider.create_client(**arguments)
        self.assertEqual(first,second)
        self.assertEqual(calls.count("/panel/api/clients/add"),1)
        self.assertIn("vless://",first["configuration"])

    def test_discovery_preserves_panel_order_and_rejects_unsafe_snapshot(self):
        def provider_for(rows):
            client=httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={"success":True,"obj":rows})))
            return SanaeiProvider("https://panel.test","token",client=client)
        provider=provider_for([{"id":9,"remark":"second"},{"id":3,"remark":"first"}])
        self.assertEqual([item["remote_id"] for item in provider.discover_inbounds()],[9,3])
        for invalid in ([{"id":3},{"id":3}], [{"id":"3"}]):
            with self.assertRaises(PanelError):
                provider_for(invalid).discover_inbounds()

    def test_authentication_failure(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(401))) as client:
            provider=SanaeiProvider("https://panel.test","bad-token",client=client)
            with self.assertRaises(PanelAuthenticationError): provider.discover_inbounds()

    def test_renewal_and_disable(self):
        bodies=[]
        def handle(request):
            if request.url.path.endswith("clients/get/alice"):
                return httpx.Response(200,json={"success":True,"obj":{"email":"alice","tgId":99,"id":"remote","totalGB":100,"enable":True}})
            if request.url.path.endswith("clients/update/alice") or request.url.path.endswith("clients/bulkDisable"):
                bodies.append(json.loads(request.content))
                return httpx.Response(200,json={"success":True})
            if request.url.path.endswith("clients/links/alice"):
                return httpx.Response(200,json={"success":True,"obj":["vless://credential@host"]})
            return httpx.Response(404)
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            provider=SanaeiProvider("https://panel.test","token",client=client)
            provider.renew_client("alice",traffic_bytes=200,expires_at_ms=1760000000000,user_limit=2)
            provider.disable_client("alice")
        self.assertEqual(bodies[0]["totalGB"],200)
        self.assertEqual(bodies[0]["limitIp"],2)
        self.assertEqual(bodies[0]["limitHwid"],0)
        self.assertEqual(set(bodies[0]),{"email","totalGB","expiryTime","tgId","limitIp","limitHwid","enable"})
        self.assertEqual(bodies[1],[{"emails":["alice"]}][0])

    def test_renewal_strips_read_only_client_fields(self):
        bodies=[]
        def handle(request):
            if request.url.path.endswith("clients/get/alice"):
                return httpx.Response(200,json={"success":True,"obj":{"email":"alice","tgId":99,"id":"remote","createdAt":1,"updatedAt":2,"limitHwid":3}})
            if request.url.path.endswith("clients/update/alice"):
                bodies.append(json.loads(request.content))
                return httpx.Response(200,json={"success":True})
            if request.url.path.endswith("clients/links/alice"):
                return httpx.Response(200,json={"success":True,"obj":["vless://credential@host"]})
            return httpx.Response(404)
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            SanaeiProvider("https://panel.test","token",client=client).renew_client("alice",traffic_bytes=200,expires_at_ms=None,user_limit=2)
        self.assertNotIn("id",bodies[0])
        self.assertNotIn("createdAt",bodies[0])
        self.assertNotIn("updatedAt",bodies[0])
        self.assertEqual(bodies[0]["limitHwid"],3)

    def test_traffic_counters_are_real_and_nonnegative(self):
        def handle(request):
            return httpx.Response(200,json={"success":True,"obj":{"email":"alice","up":1024,"down":2048}})
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            self.assertEqual(SanaeiProvider("https://panel.test","token",client=client).get_traffic("alice")["used"],3072)
        for invalid in ({"up":None,"down":0},{"up":-1,"down":0},{"up":True,"down":0},{"email":"bob","up":0,"down":0}):
            with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={"success":True,"obj":invalid}))) as client:
                with self.assertRaises(PanelError):
                    SanaeiProvider("https://panel.test","token",client=client).get_traffic("alice")

if __name__=="__main__": unittest.main()
