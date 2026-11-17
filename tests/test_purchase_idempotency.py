import unittest
from contextlib import contextmanager
from unittest.mock import patch
from fastapi import HTTPException

from alart.core import purchase


class ExistingOrderConnection:
    def __init__(self):
        self.calls = []

    def execute(self, query, params):
        self.calls.append(query)
        if "FROM orders WHERE user_id" in query:
            return Result({"plan_id": 2, "payment_method": "stars", "price_minor": 100,
                           "selection": {"users": 1, "traffic_gb": None, "duration_days": 30}, "server_id": 9})
        if "location_id FROM servers" in query:
            return Result({"location_id": 4})
        raise AssertionError("Duplicate purchase should not create financial records")


class Result:
    def __init__(self, row): self.row = row
    def fetchone(self): return self.row


@contextmanager
def existing_transaction(connection):
    yield connection


class PurchaseIdempotencyTests(unittest.TestCase):
    def test_retry_with_changed_location_is_rejected(self):
        connection = ExistingOrderConnection()
        with patch("alart.core.transaction", lambda: existing_transaction(connection)):
            with self.assertRaises(HTTPException) as error:
                purchase(1, 2, 5, "an-existing-request", expected_final_minor=100,
                         payment_method="stars", users=1, duration_days=30)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(connection.calls), 2)


if __name__ == "__main__": unittest.main()
