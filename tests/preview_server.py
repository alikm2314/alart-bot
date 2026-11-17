"""Loopback-only UI fixture. Not part of the production application routes."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
now=datetime.now(timezone.utc)
def initial():
    return {'me':{'name':'کاربر آزمایشی','language':'fa','balance_minor':1250000},'catalog':{'services':[{'id':1,'name':'تست رایگان','trial_enabled':True,'category':'volume_timed'}],'plans':[],'locations':[{'id':1}],'announcements':[]},'trial_eligible':True,'notifications':[],
     'account':{'orders':[{'id':3,'plan':'سرویس یک‌ماهه','status':'awaiting_payment','payment_method':'stars'}],'transactions':[],
      'subscriptions':[{'id':1,'plan_name':'سرویس حجمی','status':'active','expires_at':(now+timedelta(days=2,hours=5)).isoformat(),'traffic_limit_bytes':50*1024**3,'traffic_used_bytes':10*1024**3,'traffic_sampled_at':now.isoformat(),'user_limit':2,'configuration':'vless://test@example.com:443','provider_subscription_url':'https://example.com:2096/sub/test','category':'volume_timed','is_trial':False},
       {'id':2,'plan_name':'تست رایگان','status':'expired','expires_at':(now-timedelta(days=2)).isoformat(),'traffic_limit_bytes':1024**3,'traffic_used_bytes':200*1024**2,'user_limit':1,'is_trial':True}]}}
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,body,mime='application/json'):
        if isinstance(body,dict) or isinstance(body,list):body=json.dumps(body,ensure_ascii=False).encode()
        if isinstance(body,str):body=body.encode()
        self.send_response(200);self.send_header('Content-Type',mime);self.end_headers();self.wfile.write(body)
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length',0)))
        self.send({'ok':True,'bootstrap':initial()})
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/fixture-sdk.js':return self.send("window.Telegram={WebApp:{initData:'fixture',ready(){},expand(){}}}",'text/javascript')
        if path=='/api/guides':return self.send([{'platform':x,'title':'آموزش اتصال','body':'۱. لینک اشتراک را کپی کنید.\n۲. در برنامهٔ اتصال گزینهٔ Import from URL را بزنید.\n۳. لینک را وارد کنید و اتصال را روشن کنید.'} for x in ('ios','android','macos','windows')])
        if path=='/api/me':return self.send(initial()['me'])
        if path=='/api/account':return self.send(initial()['account'])
        if path.endswith('/qr'):
            from alart.qr import provider_subscription_png
            return self.send(provider_subscription_png('https://example.com:2096/sub/test'),'image/png')
        target=ROOT/'web'/path.strip('/')
        if path=='/app/':target=target/'index.html'
        if target.is_file() and target.resolve().is_relative_to(ROOT/'web'):
            if target.suffix=='.html':return self.send(target.read_text(encoding='utf-8').replace('https://telegram.org/js/telegram-web-app.js','/fixture-sdk.js'),'text/html; charset=utf-8')
            return self.send(target.read_bytes(),{'.css':'text/css','.js':'text/javascript','.png':'image/png'}.get(target.suffix,'application/octet-stream'))
        self.send_response(404);self.end_headers()
if __name__=='__main__':HTTPServer(('127.0.0.1',8765),Handler).serve_forever()
