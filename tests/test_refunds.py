import unittest
from contextlib import contextmanager
from unittest.mock import patch
from fastapi import HTTPException
from alart import refunds
from alart.payment_routes import refund_stars

class Cursor:
    def __init__(self,row=None,rows=None): self.row=row; self.rows=rows or []
    def fetchone(self): return self.row
    def fetchall(self): return self.rows

class Connection:
    def __init__(self,status='approved',sub=None,payment_status='confirmed',method='stars'):
        self.request={'id':3,'order_id':7,'status':status,'remote_disabled_at':None,'confirmed_at':None,
                      'confirmation_source':None,'confirmation_reference':None,'reason':'Customer request','service_action':'cancel'}
        self.order={'id':7,'user_id':2,'renewal_of_sub_id':None,'payment_method':method,'price_minor':50,
                    'status':'active','currency':'XTR'}
        self.payment={'id':4,'status':payment_status,'provider_reference':'charge-7'}
        self.sub=sub
        self.writes=[]
    def execute(self,sql,params=()):
        self.writes.append((sql,params))
        if sql.startswith('SELECT id,order_id FROM refund_requests'): return Cursor(self.request if self.request['status'] in ('approved','refund_pending','processing') else None)
        if sql.startswith('SELECT * FROM refund_requests WHERE id='): return Cursor(self.request)
        if sql.startswith('SELECT order_id FROM refund_requests WHERE id='): return Cursor({'order_id':7})
        if sql.startswith('SELECT * FROM refund_requests WHERE order_id='): return Cursor(self.request)
        if sql.startswith('SELECT order_id FROM payments'): return Cursor({'order_id':7})
        if sql.startswith('SELECT id FROM orders'): return Cursor({'id':7})
        if sql.startswith('SELECT * FROM orders'): return Cursor(self.order)
        if 'FROM orders o JOIN payments p' in sql: return Cursor({**self.order,'payment_id':4,'payment_status':self.payment['status'],'provider_reference':'charge-7','telegram_id':123})
        if 'FROM subscriptions x JOIN servers' in sql: return Cursor(self.sub)
        if sql.startswith('SELECT order_id FROM renewal_jobs'): return Cursor(rows=[])
        if sql.startswith('SELECT * FROM payments WHERE order_id='): return Cursor(self.payment)
        if sql.startswith('SELECT provider FROM payments'): return Cursor({'provider':self.order['payment_method']})
        if sql.startswith('SELECT id FROM refund_requests WHERE confirmation_source='): return Cursor()
        if sql.startswith("UPDATE refund_requests SET status='processing'"): self.request['status']='processing'
        if sql.startswith('UPDATE refund_requests SET confirmed_at=now()'): self.request.update(confirmed_at=True,status='completed',confirmation_source=params[0],confirmation_reference=params[1])
        return Cursor()

@contextmanager
def transaction_for(conn):
    yield conn

class RefundTests(unittest.TestCase):
    def test_admin_stars_route_queues_without_claiming_provider_success(self):
        conn=Connection()
        with patch('alart.payment_routes.transaction',lambda:transaction_for(conn)),patch('alart.payment_routes.csrf'),patch('alart.refunds.enqueue',return_value=conn.request) as queued,patch('alart.payment_routes.TelegramStars') as stars:
            result=refund_stars(4,None,{'id':1})
        self.assertEqual(result['status'],'approved')
        queued.assert_called_once()
        stars.assert_not_called()
        self.assertFalse(any("UPDATE payments SET status='refunded'" in sql for sql,_ in conn.writes))

    def test_false_stars_response_never_marks_refunded(self):
        conn=Connection(sub={'id':9,'client_email':'client-u2-001','remote_id':'remote'})
        class Provider:
            def disable_client(self,email): self.email=email
        provider=Provider()
        with patch('alart.refunds.transaction',lambda:transaction_for(conn)),patch('alart.refunds.SanaeiProvider.from_panel',return_value=provider),patch('alart.refunds.TelegramStars') as stars,patch('alart.referral.reverse_order'):
            stars.return_value.refund.return_value=False
            self.assertTrue(refunds.process_one())
        self.assertEqual(provider.email,'client-u2-001')
        self.assertTrue(any("status='manual_review'" in sql for sql,_ in conn.writes))
        self.assertFalse(any("UPDATE payments SET status='refunded'" in sql for sql,_ in conn.writes))

    def test_verified_stars_refund_records_once(self):
        conn=Connection(sub={'id':9,'client_email':'client-u2-001','remote_id':'remote'})
        with patch('alart.refunds.transaction',lambda:transaction_for(conn)),patch('alart.refunds.SanaeiProvider.from_panel') as panel,patch('alart.refunds.TelegramStars') as stars,patch('alart.referral.reverse_order') as reverse:
            stars.return_value.refund.return_value=True
            refunds.process_one()
        panel.return_value.disable_client.assert_called_once_with('client-u2-001')
        self.assertTrue(any("UPDATE payments SET status='refunded'" in sql for sql,_ in conn.writes))
        reverse.assert_called()
        self.assertEqual(conn.request['confirmation_source'],'telegram')

    def test_ambiguous_processing_never_retries_provider_call(self):
        conn=Connection(status='processing')
        with patch('alart.refunds.transaction',lambda:transaction_for(conn)),patch('alart.refunds.TelegramStars') as stars:
            refunds.process_one()
        stars.assert_not_called()
        self.assertTrue(any("status='manual_review'" in sql for sql,_ in conn.writes))

    def test_customer_cannot_request_another_users_order(self):
        class Other(Connection):
            def execute(self,sql,params=()):
                if sql.startswith('SELECT id FROM orders WHERE id=%s AND user_id=%s'): return Cursor()
                return super().execute(sql,params)
        conn=Other()
        with patch('alart.refunds.transaction',lambda:transaction_for(conn)),patch('alart.refunds.csrf'):
            with self.assertRaises(HTTPException) as ctx:
                refunds.request_cancellation(7,refunds.Cancellation(reason='Please cancel'),None,{'id':88})
        self.assertEqual(ctx.exception.status_code,404)
        self.assertFalse(any('INSERT INTO refund_requests' in sql for sql,_ in conn.writes))

    def test_manual_confirmation_rejects_stars(self):
        conn=Connection(status='manual_review')
        conn.request['remote_disabled_at']=True
        with patch('alart.refunds.transaction',lambda:transaction_for(conn)),patch('alart.refunds.csrf'):
            with self.assertRaises(HTTPException) as ctx:
                refunds.review_refund(3,refunds.Decision(action='confirm_manual',reference='verified-123'),None,{'id':1})
        self.assertEqual(ctx.exception.status_code,409)

    def test_refund_confirmation_is_idempotent(self):
        conn=Connection()
        with patch('alart.referral.reverse_order') as reverse:
            refunds.confirm(conn,conn.request,'telegram','charge-7')
            refunds.confirm(conn,conn.request,'telegram','charge-7')
        self.assertEqual(sum("UPDATE payments SET status='refunded'" in sql for sql,_ in conn.writes),1)
        reverse.assert_called_once()

    def test_failed_renewal_refund_keeps_existing_subscription(self):
        conn=Connection(method='wallet')
        conn.order['renewal_of_sub_id']=9
        conn.request['service_action']='refund_only'
        with patch('alart.refunds.transaction',lambda:transaction_for(conn)),patch('alart.refunds.SanaeiProvider.from_panel') as panel,patch('alart.payment.WalletPaymentProvider.refund') as credit,patch('alart.referral.reverse_order'):
            refunds.process_one()
        panel.assert_not_called()
        credit.assert_called_once()
        self.assertFalse(any("UPDATE subscriptions SET status='cancelled'" in sql for sql,_ in conn.writes))
        self.assertTrue(any("UPDATE renewal_jobs SET status='failed'" in sql for sql,_ in conn.writes))
