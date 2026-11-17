import unittest
from contextlib import contextmanager
from unittest.mock import patch
from alart.payment_routes import Review,manual_review


class Cursor:
    def __init__(self,row=None): self.row=row
    def fetchone(self): return self.row

class Connection:
    def __init__(self): self.calls=[]
    def execute(self,sql,params=()):
        self.calls.append((sql,params))
        if "FROM payments p JOIN orders o" in sql:
            return Cursor({"id":8,"order_id":20,"order_status":"manual_review","user_id":3,"amount_minor":5000,"currency":"IRR"})
        return Cursor()

@contextmanager
def transaction_for(conn): yield conn

class ManualReviewTests(unittest.TestCase):
    def test_explicit_approval_settles_and_audits(self):
        conn=Connection()
        with patch("alart.payment_routes.transaction",lambda:transaction_for(conn)),patch("alart.payment_routes.csrf"),patch("alart.payment_routes.settle_order") as settle:
            result=manual_review(Review(payment_id=8,action="approve"),None,{"id":1})
        self.assertEqual(result["status"],"approved")
        settle.assert_called_once()
        self.assertTrue(any("INSERT INTO audit_logs" in sql for sql,_ in conn.calls))

    def test_more_info_does_not_settle_and_notifies(self):
        conn=Connection()
        with patch("alart.payment_routes.transaction",lambda:transaction_for(conn)),patch("alart.payment_routes.csrf"),patch("alart.payment_routes.settle_order") as settle:
            result=manual_review(Review(payment_id=8,action="more_info"),None,{"id":1})
        self.assertEqual(result["status"],"more_info")
        settle.assert_not_called()
        self.assertTrue(any("INSERT INTO notifications" in sql for sql,_ in conn.calls))

if __name__=="__main__": unittest.main()
