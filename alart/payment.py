from abc import ABC, abstractmethod
from fastapi import HTTPException
from .core import ledger

class PaymentProvider(ABC):
    @abstractmethod
    def create_payment(self, conn, *, user_id: int, amount_minor: int, reference: str): ...
    @abstractmethod
    def verify_payment(self, conn, *, reference: str) -> bool: ...
    @abstractmethod
    def refund(self, conn, *, user_id: int, amount_minor: int, reference: str): ...
    @abstractmethod
    def process_callback(self, conn, *, payload: bytes, signature: str): ...
    @abstractmethod
    def get_status(self, conn, *, reference: str) -> str: ...

class WalletPaymentProvider(PaymentProvider):
    def create_payment(self, conn, *, user_id, amount_minor, reference):
        conn.execute('SELECT balance_minor FROM wallets WHERE user_id=%s FOR UPDATE',(user_id,))
        if conn.execute("""SELECT 1 FROM referral_rewards w JOIN referrals r ON r.referred_user_id=w.referred_user_id
          JOIN refund_requests f ON f.order_id=r.qualified_order_id WHERE w.beneficiary_user_id=%s
          AND w.status<>'reversed' AND f.status NOT IN ('requested','rejected','completed') LIMIT 1""",(user_id,)).fetchone():
            raise HTTPException(409,'Wallet purchases are paused pending referral reward reversal')
        if conn.execute("SELECT 1 FROM referral_rewards WHERE beneficiary_user_id=%s AND status='reversal_pending' LIMIT 1",(user_id,)).fetchone():
            raise HTTPException(409,"Wallet purchases are paused pending referral reward reversal")
        if amount_minor:
            ledger(conn,user_id,-amount_minor,"purchase",reference)
        return {"status":"confirmed","provider":"wallet"}

    def verify_payment(self, conn, *, reference):
        row=conn.execute("SELECT kind FROM wallet_transactions WHERE reference=%s",(reference,)).fetchone()
        return bool(row and row["kind"]=="purchase")

    def refund(self, conn, *, user_id, amount_minor, reference):
        if amount_minor:
            return ledger(conn,user_id,amount_minor,"refund",reference)
        return None

    def process_callback(self, conn, *, payload, signature):
        raise ValueError("Wallet payments do not use callbacks")

    def get_status(self, conn, *, reference):
        return "confirmed" if self.verify_payment(conn,reference=reference) else "unavailable"
