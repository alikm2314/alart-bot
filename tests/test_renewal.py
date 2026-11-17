import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from fastapi import HTTPException
from alart import renewal, core, worker
from alart.bot import renewal_button
from alart.localization import tr

NOW=datetime(2026,9,17,12,tzinfo=timezone.utc)
GB=1024**3

class Cursor:
    def __init__(self,row=None,rows=None): self.row=row;self.rows=rows or []
    def fetchone(self): return self.row
    def fetchall(self): return self.rows

class ProjectionTests(unittest.TestCase):
    def test_active_thirty_days_starts_from_existing_expiry(self):
        old=NOW+timedelta(days=30)
        result=renewal.project(category='unlimited',status='active',expires_at=old,traffic_limit_bytes=None,duration_hours=720,traffic_gb=None,now=NOW)
        self.assertEqual(result['target_expires_at'],NOW+timedelta(days=60))
        self.assertEqual(result['mode'],'time')

    def test_five_days_remaining_plus_thirty_keeps_all_days(self):
        result=renewal.project(category='unlimited',status='active',expires_at=NOW+timedelta(days=5),traffic_limit_bytes=None,duration_hours=720,traffic_gb=None,now=NOW)
        self.assertEqual(result['target_expires_at'],NOW+timedelta(days=35))

    def test_expired_starts_from_now_even_if_old_expiry_future(self):
        result=renewal.project(category='volume_timed',status='expired',expires_at=NOW+timedelta(days=2),traffic_limit_bytes=50*GB,duration_hours=720,traffic_gb=100,now=NOW)
        self.assertEqual(result['target_expires_at'],NOW+timedelta(days=30))
        self.assertEqual(result['target_traffic_limit_bytes'],150*GB)
        self.assertEqual(result['mode'],'time_and_traffic')

    def test_no_expiry_volume_is_traffic_topup_only(self):
        result=renewal.project(category='volume_unlimited',status='active',expires_at=None,traffic_limit_bytes=50*GB,duration_hours=0,traffic_gb=100,now=NOW)
        self.assertIsNone(result['target_expires_at'])
        self.assertEqual(result['target_traffic_limit_bytes'],150*GB)
        self.assertEqual(result['mode'],'traffic_topup')
        with self.assertRaises(HTTPException):
            renewal.project(category='volume_unlimited',status='active',expires_at=None,traffic_limit_bytes=50*GB,duration_hours=720,traffic_gb=100,now=NOW)

    def test_unlimited_duration_without_traffic_cannot_renew(self):
        with self.assertRaises(HTTPException):
            renewal.project(category='unlimited',status='active',expires_at=None,traffic_limit_bytes=None,duration_hours=0,traffic_gb=None,now=NOW)

    def test_compatible_plan_preserves_user_limit_and_service(self):
        original={'plan_id':1,'service_id':3,'category':'volume_timed','selection':{'users':2,'traffic_gb':50,'duration_days':30},'user_limit':2}
        candidate={'id':2,'service_id':3,'category':'volume_timed','is_custom':False,'user_limit':2}
        self.assertEqual(renewal.selection_for(original,candidate),{'users':None,'traffic_gb':None,'duration_days':None})
        with self.assertRaises(HTTPException): renewal.selection_for(original,{**candidate,'user_limit':3})
        with self.assertRaises(HTTPException): renewal.selection_for(original,{**candidate,'service_id':4})

    def test_owner_scoped_lookup_rejects_other_user(self):
        class Conn:
            def execute(self,sql,params):
                self.assertion=(params==(9,2) and 's.user_id=%s' in sql)
                return Cursor()
        conn=Conn()
        with self.assertRaises(HTTPException) as error: renewal.eligible(conn,2,9)
        self.assertTrue(conn.assertion)
        self.assertEqual(error.exception.status_code,404)

class RenewOrderTests(unittest.TestCase):
    def test_wallet_renewal_debits_once_and_creates_one_order_and_job(self):
        class Conn:
            def __init__(self): self.order=None;self.writes=[]
            def execute(self,sql,params=()):
                self.writes.append((sql,params))
                if sql.startswith('SELECT * FROM orders WHERE user_id='): return Cursor(self.order)
                if 'FROM plans p JOIN services s' in sql: return Cursor({'id':2,'name':'30 days','service_id':3,'category':'unlimited','is_custom':False,'user_limit':1,'duration_hours':720,'traffic_gb':None})
                if sql.startswith('INSERT INTO orders('):
                    self.order={'id':7,'renewal_of_sub_id':9,'plan_id':2,'payment_method':'wallet','price_minor':250}
                    return Cursor(self.order)
                return Cursor()
        conn=Conn();debits=[]
        class Wallet:
            def create_payment(self,*args,**kwargs): debits.append(kwargs)
        offer={'price_minor':250,'currency':'IRR','selection':{'users':1,'duration_hours':720}}
        with patch('alart.core.transaction',lambda:context(conn)),patch('alart.renewal.eligible',return_value={'id':9,'plan_id':2,'server_id':4}),patch('alart.renewal.offer',return_value=offer),patch('alart.payment.WalletPaymentProvider',Wallet):
            first=core.renew(2,9,'a-request-key-123','wallet',250,2)
            second=core.renew(2,9,'a-request-key-123','wallet',250,2)
        self.assertEqual(first,second)
        self.assertEqual(len(debits),1)
        self.assertEqual(sum('INSERT INTO orders(' in sql for sql,_ in conn.writes),1)
        self.assertEqual(sum('INSERT INTO payments(' in sql for sql,_ in conn.writes),1)
        self.assertEqual(sum('INSERT INTO renewal_jobs(' in sql for sql,_ in conn.writes),1)
        self.assertFalse(any('INSERT INTO subscriptions' in sql for sql,_ in conn.writes))

    def test_replayed_request_returns_same_order_without_new_charge(self):
        class Conn:
            def __init__(self): self.calls=[]
            def execute(self,sql,params=()):
                self.calls.append(sql)
                if sql.startswith('SELECT * FROM orders WHERE user_id='):
                    return Cursor({'id':7,'renewal_of_sub_id':9,'plan_id':2,'payment_method':'stars','price_minor':25})
                return Cursor()
        conn=Conn()
        with patch('alart.core.transaction',lambda:context(conn)):
            result=core.renew(2,9,'a-request-key-123','stars',25,2)
        self.assertEqual(result['id'],7)
        self.assertIn('pg_advisory_xact_lock',conn.calls[0])
        self.assertFalse(any('INSERT INTO orders' in sql for sql in conn.calls))

    def test_replayed_request_cannot_change_price_or_plan(self):
        class Conn:
            def execute(self,sql,params=()):
                if sql.startswith('SELECT * FROM orders WHERE user_id='):
                    return Cursor({'id':7,'renewal_of_sub_id':9,'plan_id':2,'payment_method':'stars','price_minor':25})
                return Cursor()
        with patch('alart.core.transaction',lambda:context(Conn())):
            with self.assertRaises(HTTPException): core.renew(2,9,'a-request-key-123','stars',5,2)
            with self.assertRaises(HTTPException): core.renew(2,9,'a-request-key-123','stars',25,3)

    def test_server_rejects_manipulated_final_price_before_payment(self):
        class Conn:
            def __init__(self):self.writes=[]
            def execute(self,sql,params=()):
                self.writes.append(sql)
                if 'FROM plans p JOIN services s' in sql: return Cursor({'id':2,'name':'30 days','service_id':3,'category':'unlimited','is_custom':False,'user_limit':1,'duration_hours':720,'traffic_gb':None})
                return Cursor()
        conn=Conn()
        with patch('alart.core.transaction',lambda:context(conn)),patch('alart.renewal.eligible',return_value={'id':9,'plan_id':2,'server_id':4}),patch('alart.renewal.offer',return_value={'price_minor':25,'currency':'XTR','selection':{'users':1}}):
            with self.assertRaises(HTTPException) as error:core.renew(2,9,'a-request-key-123','stars',5,2)
        self.assertEqual(error.exception.status_code,409)
        self.assertFalse(any('INSERT INTO orders' in sql or 'INSERT INTO payments' in sql for sql in conn.writes))

@contextmanager
def context(conn):yield conn

class WorkerTests(unittest.TestCase):
    def test_prepared_target_is_reused_after_remote_timeout(self):
        old=datetime.now(timezone.utc)+timedelta(days=5)
        job={'id':3,'order_id':7,'subscription_id':9,'attempts':0,'prepared_at':None,'target_expires_at':None,'target_traffic_limit_bytes':None}
        data={'selection':{'users':1,'traffic_gb':None,'duration_days':None},'user_id':2,'payment_method':'stars','duration_hours':720,
              'is_custom':False,'category':'unlimited','subscription_status':'active','expires_at':old,'traffic_limit_bytes':None,
              'user_limit':1,'client_email':'client-u2-001','panel_id':1,'base_url':'https://example.com','credential_ciphertext':'x'}
        class Conn:
            def __init__(self):self.writes=[]
            def execute(self,sql,params=()):
                self.writes.append((sql,params))
                if sql.startswith('SELECT * FROM renewal_jobs'):return Cursor(job if job.get('status')!='succeeded' else None)
                if 'FROM orders o JOIN plans p' in sql:return Cursor(data)
                if sql.startswith('UPDATE renewal_jobs SET prepared_at'):
                    job.update(prepared_at=True,target_expires_at=params[1],target_traffic_limit_bytes=params[3])
                if sql.startswith("UPDATE renewal_jobs SET status='succeeded'"):job['status']='succeeded'
                if sql.startswith('INSERT INTO alerts'):return Cursor({'id':1})
                if sql.startswith("UPDATE orders SET status='failed'"):return Cursor({'user_id':2,'price_minor':25,'payment_method':'stars'})
                return Cursor()
        conn=Conn();calls=[]
        class Provider:
            def renew_client(self,email,**kwargs):
                calls.append((email,kwargs))
                if len(calls)==1:raise RuntimeError('timeout')
                return {'configuration':'vless://same-link'}
        with patch('alart.worker.transaction',lambda:context(conn)),patch('alart.worker.SanaeiProvider.from_panel',return_value=Provider()),patch('alart.worker.time.time',return_value=NOW.timestamp()):
            worker.process_renewal()
            worker.process_renewal()
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0],calls[1])
        self.assertEqual(calls[0][0],'client-u2-001')
        self.assertEqual(calls[0][1]['expires_at_ms'],int((old+timedelta(days=30)).timestamp()*1000))
        self.assertFalse(any('INSERT INTO subscriptions' in sql for sql,_ in conn.writes))
        self.assertEqual(sum("INSERT INTO notifications" in sql for sql,_ in conn.writes),1)

    def test_reminder_has_direct_subscription_action(self):
        class Conn:
            def __init__(self):self.calls=0;self.writes=[]
            def execute(self,sql,params=()):
                self.writes.append((sql,params))
                if sql.startswith('WITH due'):
                    self.calls+=1
                    return Cursor(rows=[{'id':9,'user_id':2,'expires_at':NOW+timedelta(days=5)}] if self.calls==1 else [])
                return Cursor()
        conn=Conn()
        with patch('alart.worker.transaction',lambda:context(conn)):
            worker.remind_renewals();worker.remind_renewals()
        self.assertEqual(sum('INSERT INTO notifications' in sql for sql,_ in conn.writes),1)
        self.assertEqual(next(params for sql,params in conn.writes if 'INSERT INTO notifications' in sql)[2],'/app/?renew=9')
        with patch('alart.bot.settings',return_value={'DOMAIN':'alart.example'}):
            button=renewal_button('/app/?renew=9','fa')
        self.assertEqual(button['inline_keyboard'][0][0]['web_app']['url'],'https://alart.example/app/?renew=9')
        self.assertIn('تمدید',button['inline_keyboard'][0][0]['text'])
        self.assertIn('آلارت',tr('fa',f"Your service expires on {NOW.strftime('%Y-%m-%d %H:%M UTC')}. Renew it in Alart."))
        with self.assertRaises(ValueError):renewal_button('https://attacker.test','fa')
