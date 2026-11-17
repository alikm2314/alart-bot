import unittest
from fastapi import HTTPException
from alart.core import settle_order


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, status="awaiting_payment"):
        self.order = {"id": 4, "user_id": 2, "server_id": 8, "payment_method": "stars", "price_minor": 15, "currency": "XTR", "status": status, "renewal_of_sub_id": None}
        self.writes = []

    def execute(self, sql, params=()):
        if sql.startswith("SELECT * FROM orders"):
            return Cursor(self.order)
        self.writes.append((sql, params))
        if sql.startswith("INSERT INTO subscriptions"):
            return Cursor({"id": 10})
        return Cursor()


class SettlementTests(unittest.TestCase):
    def test_success_creates_one_job(self):
        conn = Connection()
        settle_order(conn, 4, provider="stars", reference="charge-1", amount=15, currency="XTR")
        self.assertEqual(sum("INSERT INTO provisioning_jobs" in sql for sql, _ in conn.writes), 1)
        self.assertEqual(sum("INSERT INTO subscriptions" in sql for sql, _ in conn.writes), 1)

    def test_duplicate_paid_order_has_no_side_effects(self):
        conn = Connection("paid")
        settle_order(conn, 4, provider="stars", reference="charge-1", amount=15, currency="XTR")
        self.assertEqual(conn.writes, [])

    def test_verified_renewal_creates_one_job_and_no_subscription(self):
        conn = Connection()
        conn.order['renewal_of_sub_id'] = 22
        settle_order(conn, 4, provider="stars", reference="charge-1", amount=15, currency="XTR")
        self.assertEqual(sum("INSERT INTO renewal_jobs" in sql for sql, _ in conn.writes), 1)
        self.assertEqual(sum("INSERT INTO subscriptions" in sql for sql, _ in conn.writes), 0)
        conn.order['status'] = 'paid'
        conn.writes.clear()
        settle_order(conn, 4, provider="stars", reference="charge-1", amount=15, currency="XTR")
        self.assertEqual(conn.writes, [])

    def test_amount_or_provider_mismatch_rejected(self):
        conn = Connection()
        with self.assertRaises(HTTPException):
            settle_order(conn, 4, provider="stars", reference="charge-1", amount=14, currency="XTR")
        with self.assertRaises(HTTPException):
            settle_order(conn, 4, provider="manual", reference="charge-1", amount=15, currency="XTR")
        self.assertEqual(conn.writes, [])
