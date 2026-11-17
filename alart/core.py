from fastapi import HTTPException
from .db import transaction
from .pricing import quote as price_quote
import json
import hashlib

def ledger(conn, user_id: int, amount: int, kind: str, reference: str):
    # Serialize duplicate references before reading them, including cross-user
    # attempts. A wallet row lock alone leaves a check/insert race.
    lock_key=int.from_bytes(hashlib.sha256(reference.encode()).digest()[:8],'big',signed=True)
    conn.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key,))
    existing = conn.execute("SELECT * FROM wallet_transactions WHERE reference=%s", (reference,)).fetchone()
    if existing:
        if existing["user_id"] != user_id or existing["amount_minor"] != amount or existing["kind"] != kind:
            raise HTTPException(409, "Conflicting transaction reference")
        return existing
    conn.execute("INSERT INTO wallets(user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    wallet = conn.execute("SELECT balance_minor FROM wallets WHERE user_id=%s FOR UPDATE", (user_id,)).fetchone()
    balance = wallet["balance_minor"] + amount
    if balance < 0:
        raise HTTPException(409, "Insufficient wallet balance")
    conn.execute("UPDATE wallets SET balance_minor=%s WHERE user_id=%s", (balance, user_id))
    row=conn.execute("INSERT INTO wallet_transactions(user_id,amount_minor,balance_after_minor,kind,reference) VALUES (%s,%s,%s,%s,%s) RETURNING *", (user_id,amount,balance,kind,reference)).fetchone()
    from .ops_topics import emit
    emit(conn,"wallet.changed",user_id,key=reference)
    return row

def choose_server(conn, location_id: int, service_id: int):
    return conn.execute("""SELECT s.* FROM servers s JOIN managed_panels p ON p.id=s.panel_id
      WHERE s.location_id=%s AND s.status='enabled' AND p.enabled AND p.health='online' AND p.last_ok_at>now()-interval '10 minutes'
      AND EXISTS (SELECT 1 FROM panel_inbounds i JOIN service_inbound_mappings m ON m.inbound_id=i.id
        WHERE i.panel_id=p.id AND i.allowed AND i.remote_enabled AND m.service_id=%s)
      AND (SELECT count(*) FROM subscriptions x WHERE x.server_id=s.id AND x.status IN ('pending','active','expiring'))<s.capacity
      ORDER BY (SELECT count(*) FROM subscriptions x WHERE x.server_id=s.id AND x.status IN ('pending','active','expiring')), s.id LIMIT 1 FOR UPDATE OF s SKIP LOCKED""", (location_id,service_id)).fetchone()

def settle_order(conn, order_id: int, *, provider: str, reference: str, amount: int, currency: str):
    order=conn.execute("SELECT * FROM orders WHERE id=%s FOR UPDATE",(order_id,)).fetchone()
    if not order or order["payment_method"]!=provider or order["price_minor"]!=amount or order["currency"]!=currency:
        raise HTTPException(409,"Payment does not match the order")
    if order["status"] in ("paid","provisioning","active","refunded"):
        return order
    if order['status']=='cancelled':
        previous=conn.execute("SELECT provider_reference,status FROM payments WHERE order_id=%s AND provider=%s FOR UPDATE",(order_id,provider)).fetchone()
        if previous and previous['provider_reference'] and previous['provider_reference']!=reference:
            raise HTTPException(409,'Payment reference does not match the order')
        if previous and previous['status']=='refunded':
            return order
        conn.execute("UPDATE payments SET status='confirmed',provider_reference=%s,updated_at=now() WHERE order_id=%s AND provider=%s AND status<>'refunded'",(reference,order_id,provider))
        # Verified money arriving after cancellation must be reconciled, never
        # delivered or discarded. Reopen even an already completed unpaid case.
        from .refunds import enqueue
        enqueue(conn,order_id,reason='Payment received after cancellation',approved=True)
        conn.execute("UPDATE refund_requests SET status='approved',confirmed_at=NULL,confirmation_source=NULL,run_after=now(),updated_at=now() WHERE order_id=%s AND confirmed_at IS NULL",(order_id,))
        return order
    if order["status"]!="awaiting_payment" and order["status"]!="manual_review":
        raise HTTPException(409,"Order cannot be settled")
    conn.execute("UPDATE payments SET status='confirmed',provider_reference=%s,updated_at=now() WHERE order_id=%s AND provider=%s AND status<>'confirmed'",(reference,order_id,provider))
    conn.execute("UPDATE orders SET status='paid' WHERE id=%s",(order_id,))
    from .ops_topics import emit
    emit(conn,"payment.confirmed",order_id)
    if order["renewal_of_sub_id"]:
        conn.execute("INSERT INTO renewal_jobs(order_id,subscription_id) VALUES (%s,%s) ON CONFLICT (order_id) DO NOTHING",(order_id,order["renewal_of_sub_id"]))
        return order
    sub=conn.execute("INSERT INTO subscriptions(user_id,order_id,server_id,status) VALUES (%s,%s,%s,'pending') ON CONFLICT (order_id) DO UPDATE SET status=subscriptions.status RETURNING id",(order["user_id"],order_id,order["server_id"])).fetchone()
    conn.execute("INSERT INTO provisioning_jobs(order_id,subscription_id,server_id,status,idempotency_key) VALUES (%s,%s,%s,'pending',%s) ON CONFLICT (order_id) DO NOTHING",(order_id,sub["id"],order["server_id"],f"order:{order_id}"))
    return order

def renew(user_id: int, subscription_id: int, request_key: str, payment_method: str, expected_final_minor: int, plan_id: int | None = None):
    if len(request_key)<12 or len(request_key)>100:
        raise HTTPException(422,"Invalid request key")
    with transaction() as conn:
        lock_key=int.from_bytes(hashlib.sha256(f'renew:{user_id}:{request_key}'.encode()).digest()[:8],'big',signed=True)
        conn.execute('SELECT pg_advisory_xact_lock(%s)',(lock_key,))
        existing=conn.execute("SELECT * FROM orders WHERE user_id=%s AND request_key=%s",(user_id,request_key)).fetchone()
        if existing:
            if (existing["renewal_of_sub_id"]!=subscription_id or (plan_id is not None and existing['plan_id']!=plan_id)
                or existing['payment_method']!=payment_method or existing['price_minor']!=expected_final_minor):
                raise HTTPException(409,"Request key already used")
            return existing
        from .renewal import eligible, offer
        sub=eligible(conn,user_id,subscription_id,lock=True)
        chosen_plan=plan_id or sub['plan_id']
        candidate=conn.execute("""SELECT p.id,p.name,p.service_id,p.is_custom,p.user_limit,p.duration_hours,p.traffic_gb,s.category
          FROM plans p JOIN services s ON s.id=p.service_id WHERE p.id=%s AND p.enabled AND s.enabled""",(chosen_plan,)).fetchone()
        if not candidate:
            raise HTTPException(404,'Renewal plan is unavailable')
        preview=offer(conn,sub,candidate,payment_method)
        if preview['price_minor']!=expected_final_minor:
            raise HTTPException(409,"Price changed; review the quote again")
        if payment_method=="wallet":
            from .payment import WalletPaymentProvider
            WalletPaymentProvider().create_payment(conn,user_id=user_id,amount_minor=preview['price_minor'],reference=f"renewal:{user_id}:{request_key}")
        order=conn.execute("""INSERT INTO orders(user_id,plan_id,server_id,price_minor,status,request_key,payment_method,selection,currency,renewal_of_sub_id)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",(user_id,chosen_plan,sub["server_id"],preview['price_minor'],"paid" if payment_method=="wallet" else "awaiting_payment",request_key,payment_method,json.dumps(preview['selection']),preview['currency'],subscription_id)).fetchone()
        conn.execute("INSERT INTO payments(order_id,provider,amount_minor,status,currency) VALUES (%s,%s,%s,%s,%s)",(order["id"],payment_method,preview['price_minor'],"confirmed" if payment_method=="wallet" else "pending",preview['currency']))
        if payment_method=="wallet":
            conn.execute("INSERT INTO renewal_jobs(order_id,subscription_id) VALUES (%s,%s)",(order["id"],subscription_id))
        return order

def purchase(user_id: int, plan_id: int, location_id: int, request_key: str, coupon_code: str | None = None, trial_service_id: int | None = None, expected_final_minor: int | None = None, payment_method: str = "wallet", users: int | None = None, traffic_gb: int | None = None, duration_days: int | None = None):
    if len(request_key) < 12 or len(request_key) > 100:
        raise HTTPException(422, "Invalid request key")
    with transaction() as conn:
        existing = conn.execute("SELECT * FROM orders WHERE user_id=%s AND request_key=%s", (user_id,request_key)).fetchone()
        if existing:
            selected=existing["selection"] if isinstance(existing["selection"],dict) else json.loads(existing["selection"])
            location=conn.execute("SELECT location_id FROM servers WHERE id=%s",(existing["server_id"],)).fetchone()
            if (existing["plan_id"]!=plan_id or existing["payment_method"]!=payment_method or
                (expected_final_minor is not None and existing["price_minor"]!=expected_final_minor) or
                not location or location["location_id"]!=location_id or
                any(value is not None and selected.get(key)!=value for key,value in (("users",users),("traffic_gb",traffic_gb),("duration_days",duration_days)))):
                raise HTTPException(409,"Request key already used for a different purchase")
            return existing
        priced=price_quote(conn,plan_id=plan_id,payment_method=payment_method,users=users,traffic_gb=traffic_gb,duration_days=duration_days,coupon_code=coupon_code,lock_coupon=True)
        public_free=priced.final_minor==0 and trial_service_id is None
        if public_free:
            if payment_method!="wallet" or not priced.free_purchase_enabled:
                raise HTTPException(422,"Free checkout is not enabled for this plan")
            lock_key=int.from_bytes(hashlib.sha256(f"free:{user_id}:{plan_id}".encode()).digest()[:8],"big",signed=True)
            conn.execute("SELECT pg_advisory_xact_lock(%s)",(lock_key,))
            if conn.execute("SELECT 1 FROM orders WHERE user_id=%s AND plan_id=%s AND is_public_free LIMIT 1",(user_id,plan_id)).fetchone():
                raise HTTPException(409,"Free plan already claimed")
        if trial_service_id is not None:
            if priced.service_id != trial_service_id or priced.final_minor != 0 or payment_method!="wallet":
                raise HTTPException(422,"Invalid trial plan")
            claimed=conn.execute("INSERT INTO trial_claims(user_id,service_id) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING user_id",(user_id,trial_service_id)).fetchone()
            if not claimed:
                raise HTTPException(409,"Trial already used")
        server = choose_server(conn, location_id, priced.service_id)
        if not server:
            raise HTTPException(409, "No capacity available")
        if expected_final_minor is not None and priced.final_minor != expected_final_minor:
            raise HTTPException(409,"Price changed; review the quote again")
        if payment_method=="wallet":
            from .payment import WalletPaymentProvider
            WalletPaymentProvider().create_payment(conn,user_id=user_id,amount_minor=priced.final_minor,reference=f"order:{user_id}:{request_key}")
        order = conn.execute("INSERT INTO orders(user_id,plan_id,server_id,coupon_id,price_minor,status,request_key,payment_method,selection,currency,is_public_free) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *", (user_id,plan_id,server["id"],priced.coupon_id,priced.final_minor,"paid" if payment_method=="wallet" else "awaiting_payment",request_key,payment_method,json.dumps(priced.selection),priced.currency,public_free)).fetchone()
        from .ops_topics import emit
        emit(conn,"purchase.created",order["id"])
        conn.execute("INSERT INTO payments(order_id,provider,amount_minor,status,currency) VALUES (%s,%s,%s,%s,%s)", (order["id"],payment_method,priced.final_minor,"confirmed" if payment_method=="wallet" else "pending",priced.currency))
        if payment_method=="wallet":
            sub = conn.execute("INSERT INTO subscriptions(user_id,order_id,server_id,status) VALUES (%s,%s,%s,'pending') RETURNING id", (user_id,order["id"],server["id"])).fetchone()
            conn.execute("INSERT INTO provisioning_jobs(order_id,subscription_id,server_id,status,idempotency_key) VALUES (%s,%s,%s,'pending',%s)", (order["id"],sub["id"],server["id"],f"order:{order['id']}"))
        if priced.coupon_id:
            conn.execute("UPDATE coupons SET uses=uses+1 WHERE id=%s", (priced.coupon_id,))
        return order

def trial(user_id: int, service_id: int, location_id: int):
    with transaction() as conn:
        service = conn.execute("SELECT * FROM services WHERE id=%s AND enabled AND trial_enabled", (service_id,)).fetchone()
        if not service:
            raise HTTPException(404, "Trial unavailable")
        if conn.execute("SELECT 1 FROM trial_claims WHERE user_id=%s LIMIT 1", (user_id,)).fetchone():
            raise HTTPException(409, "Trial already used")
        # Trials must create a genuinely limited remote client. Reject old
        # zero-price plans without both an expiry and a traffic allowance.
        plan = conn.execute("SELECT id FROM plans WHERE service_id=%s AND price_minor=0 AND duration_hours BETWEEN 1 AND 72 AND traffic_gb>0 AND user_limit BETWEEN 1 AND 3 AND enabled ORDER BY duration_hours LIMIT 1", (service_id,)).fetchone()
        if not plan:
            raise HTTPException(409, "A time- and traffic-limited trial plan is required")
    return purchase(user_id, plan["id"], location_id, f"trial:{user_id}:{service_id}:claim",trial_service_id=service_id)

def activate(conn, job, remote_id: str, configuration: str, expires_at_ms: int | None, traffic_limit_bytes: int | None = None, user_limit: int | None = None):
    if not remote_id or not configuration:
        raise ValueError("Provider returned incomplete service")
    conn.execute("UPDATE subscriptions SET status='active',remote_id=%s,configuration=%s,expires_at=CASE WHEN %s IS NULL THEN NULL ELSE to_timestamp(%s/1000.0) END,traffic_limit_bytes=%s,user_limit=%s WHERE id=%s AND status='pending'", (remote_id,configuration,expires_at_ms,expires_at_ms,traffic_limit_bytes,user_limit,job["subscription_id"]))
    conn.execute("UPDATE orders SET status='active' WHERE id=%s AND status IN ('paid','provisioning')", (job["order_id"],))
    conn.execute("UPDATE provisioning_jobs SET status='succeeded',lease_until=NULL WHERE id=%s AND status='processing'", (job["id"],))
    from .referral import award
    award(conn,job["order_id"])
    from .ops_topics import emit
    emit(conn,"provisioning.active",job["order_id"])
