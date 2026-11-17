import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch
from fastapi import HTTPException

from alart.api import subscription_usage


class Result:
    def __init__(self, row): self.row=row
    def fetchone(self): return self.row


class UsageConnection:
    def __init__(self, row): self.row=row;self.calls=[]
    def execute(self, sql, params):
        self.calls.append((sql,params))
        return Result(self.row)


@contextmanager
def transaction_for(conn): yield conn


class UsageTests(unittest.TestCase):
    def test_other_users_subscription_is_not_disclosed_or_queried_remotely(self):
        conn=UsageConnection(None)
        with patch('alart.api.transaction',lambda:transaction_for(conn)), patch('alart.sanaei.SanaeiProvider.from_panel') as provider:
            with self.assertRaises(HTTPException) as error:
                subscription_usage(12,user={"id":34})
            provider.assert_not_called()
        self.assertEqual(error.exception.status_code,404)
        self.assertEqual(conn.calls[0][1],(12,34))

    def test_expired_subscription_reports_only_measured_usage(self):
        sampled=datetime.now(timezone.utc)
        row={"id":12,"status":"expired","traffic_limit_bytes":1000,"traffic_used_bytes":400,
             "traffic_sampled_at":sampled,"client_email":"alice"}
        conn=UsageConnection(row)
        with patch('alart.api.transaction',lambda:transaction_for(conn)), patch('alart.sanaei.SanaeiProvider.from_panel') as provider:
            value=subscription_usage(12,user={"id":34})
            provider.assert_not_called()
        self.assertEqual(value["remaining_bytes"],600)
        self.assertFalse(value["stale"])


if __name__=="__main__": unittest.main()
