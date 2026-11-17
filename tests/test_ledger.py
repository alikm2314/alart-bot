import unittest
from fastapi import HTTPException
from alart.core import ledger

class Cursor:
    def __init__(self,row): self.row=row
    def fetchone(self): return self.row
class FakeConnection:
    def __init__(self,balance=100): self.balance=balance;self.transactions={};self.calls=[]
    def execute(self,sql,params=None):
        self.calls.append(sql)
        if sql.startswith("SELECT * FROM wallet_transactions"):
            return Cursor(self.transactions.get(params[0]))
        if sql.startswith("SELECT balance_minor"):
            return Cursor({"balance_minor":self.balance})
        if sql.startswith("UPDATE wallets"):
            self.balance=params[0]
        if sql.startswith("INSERT INTO wallet_transactions"):
            user,amount,after,kind,reference=params
            row={"user_id":user,"amount_minor":amount,"balance_after_minor":after,"kind":kind,"reference":reference}
            self.transactions[reference]=row
            return Cursor(row)
        return Cursor(None)

class LedgerTests(unittest.TestCase):
    def test_debit_and_idempotency(self):
        conn=FakeConnection()
        self.assertEqual(ledger(conn,1,-60,"purchase","order:1")["balance_after_minor"],40)
        self.assertEqual(ledger(conn,1,-60,"purchase","order:1")["balance_after_minor"],40)
        self.assertEqual(len(conn.transactions),1)
    def test_no_negative_balance(self):
        conn=FakeConnection()
        with self.assertRaises(HTTPException): ledger(conn,1,-101,"purchase","order:2")
        self.assertEqual(conn.balance,100)
    def test_conflicting_reference_rejected(self):
        conn=FakeConnection()
        ledger(conn,1,10,"deposit","ref")
        with self.assertRaises(HTTPException): ledger(conn,1,20,"deposit","ref")

    def test_reference_lock_precedes_duplicate_lookup(self):
        conn=FakeConnection()
        ledger(conn,1,-10,'purchase','same-reference')
        self.assertIn('pg_advisory_xact_lock',conn.calls[0])
        self.assertIn('wallet_transactions',conn.calls[1])

if __name__=="__main__": unittest.main()
