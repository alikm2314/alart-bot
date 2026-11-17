"""Provider calls and signature checks. Settlement lives in core.settle_order."""
from decimal import Decimal
import hashlib
import hmac
import json
import httpx
from fastapi import HTTPException
from .config import settings
from .db import transaction
from .sanaei import decrypt_token

def now_credentials() -> tuple[str,str]:
    with transaction() as conn:
        row=conn.execute("SELECT credential_ciphertext FROM payment_settings WHERE provider='nowpayments'").fetchone()
    if row and row["credential_ciphertext"]:
        values=json.loads(decrypt_token(row["credential_ciphertext"]))
        return values["api_key"],values["ipn_secret"]
    return settings()["NOWPAYMENTS_API_KEY"],settings()["NOWPAYMENTS_IPN_SECRET"]

class TelegramStars:
    def __init__(self, caller=None):
        self.caller=caller

    def _call(self, method: str, payload: dict):
        if self.caller:
            return self.caller(method,payload)
        from .bot import api
        return api(method,payload)

    def invoice_link(self, *, order_id: int, title: str, amount: int) -> tuple[str,str]:
        if amount<=0:
            raise ValueError("Stars amount must be positive")
        invoice_payload=f"alart:order:{order_id}"
        link=self._call("createInvoiceLink",{"title":title[:32],"description":f"Alart order #{order_id}","payload":invoice_payload,"provider_token":"","currency":"XTR","prices":[{"label":title[:32],"amount":amount}]})
        if not isinstance(link,str) or not link.startswith("https://"):
            raise RuntimeError("Telegram did not return an invoice link")
        return link,invoice_payload

    def wallet_invoice_link(self, *, topup_id: int, amount: int) -> tuple[str,str]:
        if amount<=0: raise ValueError("Stars amount must be positive")
        payload=f"alart:topup:{topup_id}"
        link=self._call("createInvoiceLink",{"title":"Alart wallet top-up","description":"Add credit to your Alart wallet","payload":payload,"provider_token":"","currency":"XTR","prices":[{"label":"Wallet credit","amount":amount}]})
        if not isinstance(link,str) or not link.startswith("https://"): raise RuntimeError("Telegram did not return an invoice link")
        return link,payload

    def answer_precheckout(self, query_id: str, accepted: bool, language='en'):
        payload={"pre_checkout_query_id":query_id,"ok":accepted}
        if not accepted:
            from .localization import tr
            payload["error_message"]=tr(language,"This order is no longer available. Please start checkout again.")
        self._call("answerPreCheckoutQuery",payload)

    def refund(self, *, telegram_id: int, charge_id: str):
        return self._call("refundStarPayment",{"user_id":telegram_id,"telegram_payment_charge_id":charge_id})

def stars_precheckout_matches(query: dict, payment: dict, telegram_id: int, order_status: str) -> bool:
    return (order_status=="awaiting_payment" and query.get("invoice_payload")==payment["invoice_payload"]
            and query.get("currency")=="XTR" and query.get("total_amount")==payment["amount_minor"]
            and (query.get("from") or {}).get("id")==telegram_id)

def verify_nowpayments_ipn(payload: dict, signature: str, secret: str) -> bool:
    if not secret or not signature or not isinstance(payload,dict):
        return False
    # NOWPayments signs JSON with recursively alphabetized object keys.
    def ordered(value):
        if isinstance(value,dict): return {key:ordered(value[key]) for key in sorted(value)}
        if isinstance(value,list): return [ordered(item) for item in value]
        return value
    canonical=json.dumps(ordered(payload),separators=(",",":"),ensure_ascii=False)
    expected=hmac.new(secret.encode(),canonical.encode(),hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected,signature.lower())

class NOWPayments:
    base="https://api.nowpayments.io/v1"
    def __init__(self, client: httpx.Client | None = None):
        self.client=client

    def _request(self, method: str, path: str, body: dict | None = None):
        key,_=now_credentials()
        if not key:
            raise HTTPException(503,"NOWPayments is not configured")
        headers={"x-api-key":key}
        try:
            if self.client:
                response=self.client.request(method,self.base+path,headers=headers,json=body,timeout=20)
            else:
                with httpx.Client(timeout=20,follow_redirects=False) as client:
                    response=client.request(method,self.base+path,headers=headers,json=body)
            response.raise_for_status()
            result=response.json()
        except (httpx.HTTPError,ValueError) as exc:
            raise HTTPException(502,"NOWPayments API unavailable") from exc
        if not isinstance(result,dict):
            raise HTTPException(502,"NOWPayments returned invalid data")
        return result

    def create_payment(self, *, order_id: int, usd_cents: int, pay_currency: str, callback_url: str) -> dict:
        amount=float(Decimal(usd_cents)/100)
        result=self._request("POST","/payment",{"price_amount":amount,"price_currency":"usd","pay_currency":pay_currency,"order_id":f"alart:{order_id}","order_description":f"Alart order {order_id}","ipn_callback_url":callback_url})
        if not result.get("payment_id") or not result.get("pay_address"):
            raise HTTPException(502,"NOWPayments returned incomplete payment details")
        return result

    def payment_status(self,payment_id: str) -> dict:
        return self._request("GET","/payment/"+payment_id)
