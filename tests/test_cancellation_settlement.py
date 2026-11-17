import unittest
from unittest.mock import patch
from fastapi import HTTPException
from alart.core import settle_order


class Cursor:
    def __init__(self, row=None): self.row=row
    def fetchone(self): return self.row


class Connection:
    def __init__(self, reference=None, payment_status='waiting'):
        self.reference=reference
        self.payment_status=payment_status
        self.writes=[]

    def execute(self, sql, params=()):
        self.writes.append((sql,params))
        if sql.startswith('SELECT * FROM orders WHERE id='):
            return Cursor({'id':7,'payment_method':'stars','price_minor':50,'currency':'XTR','status':'cancelled'})
        if sql.startswith('SELECT provider_reference,status FROM payments'):
            return Cursor({'provider_reference':self.reference,'status':self.payment_status})
        return Cursor()


class CancellationSettlementTests(unittest.TestCase):
    def test_late_verified_charge_enters_refund_queue_without_provisioning(self):
        conn=Connection()
        with patch('alart.refunds.enqueue') as enqueue:
            result=settle_order(conn,7,provider='stars',reference='charge-7',amount=50,currency='XTR')
        self.assertEqual(result['status'],'cancelled')
        enqueue.assert_called_once()
        self.assertTrue(any("SET status='confirmed'" in sql for sql,_ in conn.writes))
        self.assertFalse(any('INSERT INTO subscriptions' in sql or 'INSERT INTO provisioning_jobs' in sql for sql,_ in conn.writes))

    def test_second_charge_cannot_replace_first_charge(self):
        conn=Connection(reference='charge-7')
        with self.assertRaises(HTTPException) as error:
            settle_order(conn,7,provider='stars',reference='charge-8',amount=50,currency='XTR')
        self.assertEqual(error.exception.status_code,409)
        self.assertFalse(any('UPDATE payments' in sql for sql,_ in conn.writes))

    def test_replayed_charge_after_refund_does_not_reopen_order(self):
        conn=Connection(reference='charge-7',payment_status='refunded')
        with patch('alart.refunds.enqueue') as enqueue:
            settle_order(conn,7,provider='stars',reference='charge-7',amount=50,currency='XTR')
        enqueue.assert_not_called()
        self.assertFalse(any('UPDATE payments' in sql for sql,_ in conn.writes))
