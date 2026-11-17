import unittest
from contextlib import contextmanager
from unittest.mock import patch
from fastapi import HTTPException
from alart import core, worker


class Cursor:
    def __init__(self, row=None, rows=None):
        self.row, self.rows = row, rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class TrialConnection:
    def __init__(self, plan):
        self.plan = plan

    def execute(self, sql, params=()):
        if sql.startswith("SELECT * FROM services"):
            return Cursor({"id": 3})
        if sql.startswith("SELECT 1 FROM trial_claims"):
            return Cursor()
        if sql.startswith("SELECT id FROM plans"):
            assert "traffic_gb>0" in sql
            assert "duration_hours BETWEEN 1 AND 72" in sql
            return Cursor(self.plan)
        raise AssertionError(sql)


class ExpiryConnection:
    def __init__(self):
        self.writes = []

    def execute(self, sql, params=()):
        if "FROM subscriptions x" in sql:
            return Cursor(rows=[{"id": 9, "user_id": 2, "client_email": "client-u2-001", "base_url": "https://panel.test", "credential_ciphertext": "cipher", "remote_id": "remote"}])
        self.writes.append((sql, params))
        if "INSERT INTO alerts" in sql:
            return Cursor({"id": 1})
        return Cursor()


@contextmanager
def transaction_for(conn):
    yield conn


class LifecycleTests(unittest.TestCase):
    def test_trial_requires_real_limits(self):
        conn = TrialConnection(None)
        with patch("alart.core.transaction", lambda: transaction_for(conn)):
            with self.assertRaises(HTTPException):
                core.trial(2, 3, 4)
        conn.plan = {"id": 5}
        with patch("alart.core.transaction", lambda: transaction_for(conn)), patch("alart.core.purchase", return_value={"id": 7}) as purchase:
            core.trial(2, 3, 4)
            self.assertEqual(purchase.call_args.kwargs["trial_service_id"], 3)

    def test_expiration_disables_remote_before_state_change(self):
        conn = ExpiryConnection()
        calls = []
        class Provider:
            def disable_client(self, email):
                calls.append(email)
        with patch("alart.worker.transaction", lambda: transaction_for(conn)), patch("alart.worker.SanaeiProvider.from_panel", return_value=Provider()):
            worker.expire_remote_services()
        self.assertEqual(calls, ["client-u2-001"])
        self.assertTrue(any("SET status='expired'" in sql for sql, _ in conn.writes))

    def test_failed_remote_disable_does_not_expire_local_record(self):
        conn = ExpiryConnection()
        class Provider:
            def disable_client(self, email):
                raise RuntimeError("remote unavailable")
        with patch("alart.worker.transaction", lambda: transaction_for(conn)), patch("alart.worker.SanaeiProvider.from_panel", return_value=Provider()):
            worker.expire_remote_services()
        self.assertFalse(any("SET status='expired'" in sql for sql, _ in conn.writes))
        self.assertTrue(any("INSERT INTO alerts" in sql for sql, _ in conn.writes))
