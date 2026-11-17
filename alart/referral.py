"""Referral qualification happens only after verified payment and provisioning."""
import base64
import hashlib
import hmac
import re
from functools import lru_cache
import httpx
from .config import settings
from .core import ledger
from .localization import tr
from .money import toman

CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16}$")


def code_for(user_id: int) -> str:
    digest = hmac.new(settings()["SESSION_SECRET"].encode(), f"referral:{user_id}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:12]).decode().rstrip("=")


def ensure_code(conn, user_id: int) -> str:
    code = code_for(user_id)
    row = conn.execute("INSERT INTO referral_codes(user_id,code) VALUES (%s,%s) ON CONFLICT (user_id) DO UPDATE SET code=referral_codes.code RETURNING code", (user_id,code)).fetchone()
    return row["code"]


@lru_cache(maxsize=1)
def bot_username() -> str | None:
    try:
        response=httpx.get(f"https://api.telegram.org/bot{settings()['BOT_TOKEN']}/getMe",timeout=8)
        response.raise_for_status()
        value=response.json()
        username=str(value["result"]["username"])
        return username if value.get("ok") and re.fullmatch(r"[A-Za-z0-9_]{5,32}",username) else None
    except (httpx.HTTPError,KeyError,ValueError,TypeError):
        return None


def referral_link(code: str) -> str | None:
    username=bot_username()
    return f"https://t.me/{username}?start=r_{code}" if username and CODE_PATTERN.fullmatch(code) else None


def claim(conn, referred_user_id: int, code: str) -> bool:
    if not CODE_PATTERN.fullmatch(code):
        return False
    row=conn.execute("""INSERT INTO referrals(referred_user_id,referrer_user_id)
      SELECT %s,rc.user_id FROM referral_codes rc
      WHERE rc.code=%s AND rc.user_id<>%s
        AND NOT EXISTS (SELECT 1 FROM orders WHERE user_id=%s)
      ON CONFLICT (referred_user_id) DO NOTHING RETURNING referred_user_id""",
      (referred_user_id,code,referred_user_id,referred_user_id)).fetchone()
    return bool(row)


def award(conn, order_id: int) -> bool:
    order=conn.execute("""SELECT o.id,o.user_id,o.status,o.price_minor,o.renewal_of_sub_id,p.status AS payment_status
      FROM orders o JOIN payments p ON p.order_id=o.id WHERE o.id=%s FOR UPDATE OF o""",(order_id,)).fetchone()
    if not order or order["status"]!="active" or order["price_minor"]<=0 or order["renewal_of_sub_id"] is not None or order["payment_status"]!="confirmed":
        return False
    referral=conn.execute("SELECT * FROM referrals WHERE referred_user_id=%s FOR UPDATE",(order["user_id"],)).fetchone()
    if not referral or referral["status"]!="pending":
        return False
    prior=conn.execute("SELECT 1 FROM orders WHERE user_id=%s AND id<>%s AND status='active' AND price_minor>0 AND renewal_of_sub_id IS NULL LIMIT 1",(order["user_id"],order_id)).fetchone()
    if prior:
        return False
    config=conn.execute("SELECT * FROM referral_settings WHERE singleton=true").fetchone()
    if not config or not config["enabled"]:
        return False
    for role,beneficiary,amount in (("referrer",referral["referrer_user_id"],config["referrer_reward_minor"]),("referred",order["user_id"],config["referred_reward_minor"])):
        if amount<=0:
            continue
        ledger(conn,beneficiary,amount,"referral_reward",f"referral:{order['user_id']}:{role}")
        conn.execute("INSERT INTO referral_rewards(referred_user_id,beneficiary_user_id,role,amount_minor) VALUES (%s,%s,%s,%s) ON CONFLICT (referred_user_id,role) DO NOTHING",(order["user_id"],beneficiary,role,amount))
        if role=="referrer":
            person=conn.execute("SELECT language FROM users WHERE id=%s",(beneficiary,)).fetchone()
            language=person["language"] if person else "en"
            body=tr(language,"🎁 Referral reward\nYour referred user completed their first purchase. {amount} has been added to your wallet.",amount=toman(amount,language or "en"))
            conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)",(beneficiary,body))
    conn.execute("UPDATE referrals SET status='awarded',qualified_order_id=%s,qualified_at=now() WHERE referred_user_id=%s AND status='pending'",(order_id,order["user_id"]))
    from .ops_topics import emit
    emit(conn,"referral.awarded",order_id)
    return True


def reverse_order(conn, order_id: int) -> bool:
    order=conn.execute("SELECT status FROM orders WHERE id=%s FOR UPDATE",(order_id,)).fetchone()
    if not order or order["status"] not in ('refunded','cancelled'):
        return False
    referral=conn.execute("SELECT * FROM referrals WHERE qualified_order_id=%s FOR UPDATE",(order_id,)).fetchone()
    if not referral or referral["status"] in ("pending","reversed"):
        return False
    rewards=conn.execute("SELECT * FROM referral_rewards WHERE referred_user_id=%s AND status<>'reversed' FOR UPDATE",(referral["referred_user_id"],)).fetchall()
    for reward in rewards:
        wallet=conn.execute("SELECT balance_minor FROM wallets WHERE user_id=%s FOR UPDATE",(reward["beneficiary_user_id"],)).fetchone()
        if wallet and wallet["balance_minor"]>=reward["amount_minor"]:
            ledger(conn,reward["beneficiary_user_id"],-reward["amount_minor"],"referral_reversal",f"referral-reversal:{reward['id']}")
            conn.execute("UPDATE referral_rewards SET status='reversed',reversed_at=now() WHERE id=%s",(reward["id"],))
            _notify_reversal(conn,reward["beneficiary_user_id"],"Your referral reward was reversed after the qualifying order was refunded.")
        else:
            conn.execute("UPDATE referral_rewards SET status='reversal_pending' WHERE id=%s",(reward["id"],))
            if reward["status"]!="reversal_pending":
                _notify_reversal(conn,reward["beneficiary_user_id"],"A refunded referral reward is awaiting reversal. Wallet purchases are paused until the balance covers it.")
    _update_referral_status(conn,referral["referred_user_id"])
    from .ops_topics import emit
    emit(conn,"referral.reversed",order_id)
    return True


def _update_referral_status(conn, referred_user_id: int):
    pending=conn.execute("SELECT 1 FROM referral_rewards WHERE referred_user_id=%s AND status<>'reversed' LIMIT 1",(referred_user_id,)).fetchone()
    conn.execute("UPDATE referrals SET status=%s WHERE referred_user_id=%s",("reversal_pending" if pending else "reversed",referred_user_id))


def _notify_reversal(conn, user_id: int, template: str):
    person=conn.execute("SELECT language FROM users WHERE id=%s",(user_id,)).fetchone()
    conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)",(user_id,tr(person["language"] if person else "en",template)))


def retry_pending_reversals(conn, limit: int = 20) -> int:
    rewards=conn.execute("SELECT * FROM referral_rewards WHERE status='reversal_pending' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT %s",(limit,)).fetchall()
    completed=0
    for reward in rewards:
        wallet=conn.execute("SELECT balance_minor FROM wallets WHERE user_id=%s FOR UPDATE",(reward["beneficiary_user_id"],)).fetchone()
        if not wallet or wallet["balance_minor"]<reward["amount_minor"]:
            continue
        ledger(conn,reward["beneficiary_user_id"],-reward["amount_minor"],"referral_reversal",f"referral-reversal:{reward['id']}")
        conn.execute("UPDATE referral_rewards SET status='reversed',reversed_at=now() WHERE id=%s",(reward["id"],))
        _notify_reversal(conn,reward["beneficiary_user_id"],"Your referral reward was reversed after the qualifying order was refunded.")
        _update_referral_status(conn,reward["referred_user_id"])
        completed+=1
    return completed
