import json
import os
import hashlib
import uuid
from pathlib import Path
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .auth import current_user, require_operator, csrf
from .config import settings
from .core import settle_order
from .db import transaction
from .payment_integrations import TelegramStars, NOWPayments, verify_nowpayments_ipn, now_credentials

router=APIRouter(prefix="/api")
MAX_RECEIPT_BYTES=3*1024*1024
RECEIPT_TYPES={"image/jpeg":(b"\xff\xd8\xff",),"image/png":(b"\x89PNG\r\n\x1a\n",),"image/webp":(b"RIFF",)}

def _receipt_dir():
    path=Path(os.environ.get("PRIVATE_DATA_DIR","/srv/data"))/"receipts"
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    return path

def _valid_receipt(data:bytes,content_type:str):
    if not data or len(data)>MAX_RECEIPT_BYTES or content_type not in RECEIPT_TYPES:
        raise HTTPException(415,"Receipt must be a JPEG, PNG, or WebP image under 3 MB")
    if not any(data.startswith(magic) for magic in RECEIPT_TYPES[content_type]):
        raise HTTPException(415,"Receipt image format does not match its content type")
    if content_type=="image/webp" and data[8:12]!=b"WEBP":
        raise HTTPException(415,"Invalid WebP receipt")

@router.get("/orders/{order_id}/status")
def order_status(order_id:int,user=Depends(current_user)):
    with transaction() as conn:
        row=conn.execute("SELECT o.id,o.status,o.payment_method,p.status AS payment_status,m.review_status,s.id AS subscription_id,r.status AS refund_status,r.confirmed_at AS refund_confirmed_at,r.remote_disabled_at FROM orders o JOIN payments p ON p.order_id=o.id LEFT JOIN manual_payment_submissions m ON m.payment_id=p.id LEFT JOIN subscriptions s ON s.order_id=o.id LEFT JOIN refund_requests r ON r.order_id=o.id WHERE o.id=%s AND o.user_id=%s",(order_id,user["id"])).fetchone()
    if not row:
        raise HTTPException(404,"Order not found")
    return row

class ManualSubmission(BaseModel):
    order_id:int
    reference:str=Field(min_length=4,max_length=200)

class Review(BaseModel):
    payment_id:int
    action:str=Field(pattern="^(approve|reject|more_info)$")
    note:str=Field(default="",max_length=500)

@router.post("/admin/payments/stars/{payment_id}/refund")
def refund_stars(payment_id:int,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        row=conn.execute("SELECT order_id FROM payments WHERE id=%s AND provider='stars'",(payment_id,)).fetchone()
        if not row: raise HTTPException(404,'Stars payment not found')
        from .refunds import enqueue
        result=enqueue(conn,row['order_id'],actor=admin['id'],reason='Operator requested Stars refund',approved=True)
    return {'status':result['status'],'refund_id':result['id']}


def _configured(conn,provider):
    row=conn.execute("SELECT * FROM payment_settings WHERE provider=%s AND enabled",(provider,)).fetchone()
    if not row:
        raise HTTPException(409,'Payment method is unavailable')
    return row

@router.post("/payments/stars/{order_id}/invoice")
def stars_invoice(order_id:int,request:Request,user=Depends(current_user)):
    csrf(request)
    with transaction() as conn:
        _configured(conn,"stars")
        row=conn.execute("""SELECT o.id,o.status,o.user_id,o.price_minor,o.currency,p.name AS plan_name
          FROM orders o JOIN plans p ON p.id=o.plan_id WHERE o.id=%s AND o.user_id=%s AND o.payment_method='stars'""",(order_id,user["id"])).fetchone()
        if not row or row["status"]!="awaiting_payment" or row["currency"]!="XTR":
            raise HTTPException(409,"Stars checkout is unavailable for this order")
    link,payload=TelegramStars().invoice_link(order_id=order_id,title=row["plan_name"],amount=row["price_minor"])
    with transaction() as conn:
        conn.execute("UPDATE payments SET invoice_payload=%s,updated_at=now() WHERE order_id=%s AND provider='stars' AND status='pending'",(payload,order_id))
    return {"invoice_url":link,"order_id":order_id}

@router.post("/payments/nowpayments/{order_id}/create")
def nowpayments_create(order_id:int,request:Request,user=Depends(current_user)):
    csrf(request)
    with transaction() as conn:
        config=_configured(conn,"nowpayments")["config"]
        row=conn.execute("SELECT o.*,p.provider_reference,p.provider_data FROM orders o JOIN payments p ON p.order_id=o.id WHERE o.id=%s AND o.user_id=%s AND o.payment_method='nowpayments' FOR UPDATE OF o,p",(order_id,user["id"])).fetchone()
        if not row or row["status"]!="awaiting_payment" or row["currency"]!="USD":
            raise HTTPException(409,"Crypto checkout is unavailable for this order")
        if row["provider_reference"]:
            return {"payment_id":row["provider_reference"],"payment":row["provider_data"]}
        pay_currency=config.get("pay_currency")
        if not pay_currency:
            raise HTTPException(409,"NOWPayments currency is not configured")
        # Keep the order row locked through the provider call. A second checkout
        # request waits, then reuses this payment instead of creating an orphan.
        callback=f"https://{settings()['DOMAIN']}/api/payments/nowpayments/ipn"
        created=NOWPayments().create_payment(order_id=order_id,usd_cents=row["price_minor"],pay_currency=pay_currency,callback_url=callback)
        payment_id=str(created["payment_id"])
        shown={key:created.get(key) for key in ("payment_id","payment_status","pay_address","pay_amount","pay_currency","price_amount","price_currency","expiration_estimate_date")}
        conn.execute("UPDATE payments SET provider_reference=%s,provider_data=%s,status='waiting',updated_at=now() WHERE order_id=%s AND provider='nowpayments' AND provider_reference IS NULL",(payment_id,json.dumps(shown),order_id))
    return {"payment_id":payment_id,"payment":shown}

@router.post("/payments/nowpayments/ipn")
async def nowpayments_ipn(request:Request):
    raw=await request.body()
    if len(raw)>65536:
        raise HTTPException(413,"Callback too large")
    try:
        event=json.loads(raw)
    except (ValueError,UnicodeDecodeError):
        raise HTTPException(400,"Invalid callback")
    signature=request.headers.get("x-nowpayments-sig","")
    if not verify_nowpayments_ipn(event,signature,now_credentials()[1]):
        raise HTTPException(401,"Invalid callback signature")
    payment_id=str(event.get("payment_id", ""))
    if not payment_id.isdigit():
        raise HTTPException(400,"Invalid payment ID")
    verified=NOWPayments().payment_status(payment_id)
    if str(verified.get("payment_id"))!=payment_id or str(verified.get("order_id", ""))!=str(event.get("order_id", "")):
        raise HTTPException(409,"Provider payment mismatch")
    status=str(verified.get("payment_status",""))
    with transaction() as conn:
        payment=conn.execute("SELECT p.*,o.status AS order_status,o.user_id,o.renewal_of_sub_id FROM payments p JOIN orders o ON o.id=p.order_id WHERE p.provider='nowpayments' AND p.provider_reference=%s FOR UPDATE OF p,o",(payment_id,)).fetchone()
        if not payment or verified.get("order_id")!=f"alart:{payment['order_id']}":
            raise HTTPException(404,"Payment not found")
        try:
            amount=Decimal(str(verified["price_amount"]))
        except (KeyError,InvalidOperation):
            raise HTTPException(409,"Provider amount unavailable")
        if amount*100!=payment["amount_minor"] or str(verified.get("price_currency","")).upper()!=payment["currency"]:
            raise HTTPException(409,"Provider amount mismatch")
        event_key=payment_id+":"+str(verified.get("updated_at") or status)
        conn.execute("INSERT INTO payment_events(payment_id,event_key,provider_status,detail) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",(payment["id"],event_key,status,json.dumps({"payment_id":payment_id,"status":status})))
        if status=="finished":
            settle_order(conn,payment["order_id"],provider="nowpayments",reference=payment_id,amount=payment["amount_minor"],currency="USD")
        elif status=='refunded' and payment['status'] in ('confirmed','refunded'):
            from .refunds import provider_refunded
            provider_refunded(conn,payment['order_id'],'nowpayments',payment_id)
        elif status in ("waiting","confirming","confirmed","sending","partially_paid") and payment["order_status"]=="awaiting_payment":
            conn.execute("UPDATE payments SET status=%s,updated_at=now() WHERE id=%s",("confirming" if status!="waiting" else "waiting",payment["id"]))
        elif status in ("failed","expired","refunded") and payment["order_status"]=="awaiting_payment":
            conn.execute("UPDATE payments SET status=%s,updated_at=now() WHERE id=%s",("expired" if status=="expired" else "failed",payment["id"]))
            conn.execute("UPDATE orders SET status=%s WHERE id=%s",("expired" if status=="expired" else "failed",payment["order_id"]))
            if payment['renewal_of_sub_id']:
                conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)",(payment['user_id'],"Your renewal payment failed or expired. Your existing service was not changed."))
    return {"ok":True}

@router.post("/payments/manual/submit")
def manual_submit(body:ManualSubmission,request:Request,user=Depends(current_user)):
    csrf(request)
    with transaction() as conn:
        config=_configured(conn,"manual")
        payment=conn.execute("SELECT p.id,o.status,m.review_status,m.receipt_path FROM payments p JOIN orders o ON o.id=p.order_id LEFT JOIN manual_payment_submissions m ON m.payment_id=p.id WHERE o.id=%s AND o.user_id=%s AND p.provider='manual' FOR UPDATE OF p,o",(body.order_id,user["id"])).fetchone()
        if payment and payment['status']=='manual_review' and payment['review_status']=='manual_review' and not payment['receipt_path']:
            return {"status":"manual_review"}
        if not payment or (payment["status"]!="awaiting_payment" and not (payment["status"]=="manual_review" and payment["review_status"]=="more_info")):
            raise HTTPException(409,"Manual payment is unavailable")
        conn.execute("INSERT INTO manual_payment_submissions(payment_id,customer_reference) VALUES (%s,%s) ON CONFLICT (payment_id) DO UPDATE SET customer_reference=EXCLUDED.customer_reference,review_status='manual_review',receipt_path=NULL",(payment["id"],body.reference))
        conn.execute("UPDATE payments SET status='manual_review',updated_at=now() WHERE id=%s",(payment["id"],))
        conn.execute("UPDATE orders SET status='manual_review' WHERE id=%s",(body.order_id,))
        from .ops_topics import emit
        emit(conn,"payment.manual_review",body.order_id)
    return {"status":"manual_review"}

@router.put("/payments/manual/{order_id}/receipt")
async def manual_receipt(order_id:int,request:Request,user=Depends(current_user)):
    csrf(request)
    content_type=request.headers.get("content-type","").split(";",1)[0].lower()
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>MAX_RECEIPT_BYTES: raise HTTPException(413,"Receipt too large")
    data=bytes(data)
    _valid_receipt(data,content_type)
    target=None
    try:
        with transaction() as conn:
            payment=conn.execute("""SELECT p.id,m.review_status,m.receipt_path FROM payments p JOIN orders o ON o.id=p.order_id
              JOIN manual_payment_submissions m ON m.payment_id=p.id
              WHERE o.id=%s AND o.user_id=%s AND o.payment_method='manual' AND o.status='manual_review'
              FOR UPDATE OF p,o,m""",(order_id,user["id"])).fetchone()
            if not payment or payment["review_status"] not in ("manual_review","more_info"):
                raise HTTPException(409,"Receipt cannot be uploaded for this order")
            if payment["receipt_path"] and payment["review_status"]=="manual_review":
                raise HTTPException(409,"A receipt has already been submitted for review")
            name=f"{uuid.uuid4().hex}.receipt"
            target=_receipt_dir()/name
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,"wb") as output: output.write(data)
            conn.execute("UPDATE manual_payment_submissions SET receipt_path=%s,review_status='manual_review' WHERE payment_id=%s",(name,payment["id"]))
            conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id,detail) VALUES (%s,'manual.receipt_uploaded','payment',%s,%s)",(user["id"],payment["id"],json.dumps({"sha256":hashlib.sha256(data).hexdigest(),"size":len(data)})))
    except Exception:
        if target: target.unlink(missing_ok=True)
        raise
    # Keep old files for audit until a retention policy is configured.
    return {"status":"manual_review","receipt_uploaded":True}

@router.get("/admin/payments/manual/{payment_id}/receipt")
def admin_receipt(payment_id:int,admin=Depends(require_operator)):
    with transaction() as conn:
        row=conn.execute("SELECT m.receipt_path FROM manual_payment_submissions m JOIN payments p ON p.id=m.payment_id WHERE m.payment_id=%s AND p.provider='manual'",(payment_id,)).fetchone()
    if not row or not row["receipt_path"] or Path(row["receipt_path"]).name!=row["receipt_path"]:
        raise HTTPException(404,"Receipt not found")
    target=_receipt_dir()/row["receipt_path"]
    if not target.is_file(): raise HTTPException(404,"Receipt not found")
    return FileResponse(target,media_type="application/octet-stream",filename=f"payment-{payment_id}-receipt",headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Content-Security-Policy":"default-src 'none'"})

@router.get("/payments/manual/{order_id}/instructions")
def manual_instructions(order_id:int,user=Depends(current_user)):
    with transaction() as conn:
        config=_configured(conn,"manual")
        order=conn.execute("SELECT id,price_minor,currency,status FROM orders WHERE id=%s AND user_id=%s AND payment_method='manual'",(order_id,user["id"])).fetchone()
    if not order:
        raise HTTPException(404,"Manual order not found")
    return {"order_id":order_id,"amount_minor":order["price_minor"],"currency":order["currency"],"status":order["status"],"destination":config["public_details"]}

@router.post("/admin/payments/manual/review")
def manual_review(body:Review,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        payment=conn.execute("SELECT p.*,o.status AS order_status,o.user_id FROM payments p JOIN orders o ON o.id=p.order_id WHERE p.id=%s AND p.provider='manual' FOR UPDATE OF p,o",(body.payment_id,)).fetchone()
        if not payment or payment["order_status"]!="manual_review":
            raise HTTPException(409,"Manual payment is not awaiting review")
        new_status={"approve":"approved","reject":"rejected","more_info":"more_info"}[body.action]
        conn.execute("UPDATE manual_payment_submissions SET review_status=%s,reviewed_by=%s,reviewed_at=now() WHERE payment_id=%s",(new_status,admin["id"],payment["id"]))
        if body.action=="approve":
            settle_order(conn,payment["order_id"],provider="manual",reference=f"manual:{payment['id']}",amount=payment["amount_minor"],currency=payment["currency"])
        elif body.action=="reject":
            conn.execute("UPDATE payments SET status='rejected' WHERE id=%s",(payment["id"],))
            conn.execute("UPDATE orders SET status='rejected' WHERE id=%s",(payment["order_id"],))
            conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)",(payment["user_id"],"Your manual payment was rejected. Open Alart for details."))
        else:
            conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)",(payment["user_id"],"More information is needed for your manual payment. Open the web shop to update your reference or receipt."))
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id,detail) VALUES (%s,%s,'payment',%s,%s)",(admin["id"],"manual."+body.action,payment["id"],json.dumps({"note":body.note})))
        if body.action in ("approve","reject"):
            from .ops_topics import emit
            emit(conn,"payment.manual_approved" if body.action=="approve" else "payment.manual_rejected",payment["id"])
    return {"status":new_status}

@router.get("/admin/payments")
def admin_payments(admin=Depends(require_operator)):
    with transaction() as conn:
        return conn.execute("SELECT p.id,p.order_id,p.provider,p.provider_reference,p.amount_minor,p.currency,p.status,p.created_at,p.updated_at,o.user_id,o.status AS order_status,m.customer_reference,m.review_status,(m.receipt_path IS NOT NULL) AS has_receipt FROM payments p JOIN orders o ON o.id=p.order_id LEFT JOIN manual_payment_submissions m ON m.payment_id=p.id ORDER BY p.id DESC LIMIT 100").fetchall()

class WalletTopupCreate(BaseModel):
    amount_minor:int=Field(gt=0,le=10_000_000_000)
    request_key:str=Field(min_length=12,max_length=160)

class WalletTopupSubmit(BaseModel):
    reference:str=Field(default="",max_length=200)

class WalletTopupReview(BaseModel):
    action:str=Field(pattern="^(approve|reject|more_info|adjust)$")
    amount_minor:int|None=Field(default=None,gt=0,le=10_000_000_000)
    note:str=Field(default="",max_length=500)

def _store_receipt(data:bytes):
    name=f"{uuid.uuid4().hex}.receipt"
    target=_receipt_dir()/name
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"wb") as output: output.write(data)
    return name,target

async def _read_receipt(request:Request):
    content_type=request.headers.get("content-type","").split(";",1)[0].lower()
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>MAX_RECEIPT_BYTES: raise HTTPException(413,"Receipt too large")
    result=bytes(data)
    _valid_receipt(result,content_type)
    return result

@router.post("/wallet/topups/stars")
def wallet_topup_stars(body:WalletTopupCreate,request:Request,user=Depends(current_user)):
    csrf(request)
    from .stars_pricing import refresh_stored_reference,settings as star_settings,toman_to_stars
    refresh_stored_reference(force=True)
    with transaction() as conn:
        _configured(conn,"stars")
        amount=toman_to_stars(body.amount_minor,star_settings(conn))
        row=conn.execute("""INSERT INTO wallet_topups(user_id,amount_minor,amount_minor_irr,provider,currency,request_key)
          VALUES (%s,%s,%s,'stars','XTR',%s) ON CONFLICT (user_id,request_key) DO UPDATE SET request_key=EXCLUDED.request_key
          RETURNING id,amount_minor""",(user["id"],amount,body.amount_minor,body.request_key)).fetchone()
    link,payload=TelegramStars().wallet_invoice_link(topup_id=row["id"],amount=row["amount_minor"])
    with transaction() as conn:
        conn.execute("UPDATE wallet_topups SET invoice_payload=%s WHERE id=%s AND provider='stars'",(payload,row["id"]))
    return {"id":row["id"],"amount_minor":row["amount_minor"],"currency":"XTR","invoice_url":link,"credited_minor":body.amount_minor}

@router.post("/wallet/topups/manual")
def wallet_topup_create(body:WalletTopupCreate,request:Request,user=Depends(current_user)):
    csrf(request)
    with transaction() as conn:
        config=_configured(conn,"manual")
        row=conn.execute("""INSERT INTO wallet_topups(user_id,amount_minor,amount_minor_irr,request_key)
          VALUES (%s,%s,%s,%s) ON CONFLICT (user_id,request_key) DO UPDATE SET request_key=EXCLUDED.request_key
          RETURNING id,amount_minor,status""",(user["id"],body.amount_minor,body.amount_minor,body.request_key)).fetchone()
    return {"id":row["id"],"amount_minor":row["amount_minor"],"currency":"IRR","status":row["status"],"destination":config["public_details"]}

@router.post("/wallet/topups/{topup_id}/submit")
def wallet_topup_submit(topup_id:int,body:WalletTopupSubmit,request:Request,user=Depends(current_user)):
    csrf(request)
    with transaction() as conn:
        _configured(conn,"manual")
        row=conn.execute("SELECT * FROM wallet_topups WHERE id=%s AND user_id=%s FOR UPDATE",(topup_id,user["id"])).fetchone()
        if not row or row["status"] not in ("awaiting_payment","more_info"):
            raise HTTPException(409,"Top-up is not awaiting a receipt")
        conn.execute("UPDATE wallet_topups SET customer_reference=%s,status='manual_review',updated_at=now() WHERE id=%s",(body.reference.strip(),topup_id))
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id,detail) VALUES (%s,'wallet_topup.submitted','wallet_topup',%s,'{}')",(user["id"],topup_id))
    return {"status":"manual_review"}

@router.put("/wallet/topups/{topup_id}/receipt")
async def wallet_topup_receipt(topup_id:int,request:Request,user=Depends(current_user)):
    csrf(request); data=await _read_receipt(request); target=None
    try:
        with transaction() as conn:
            row=conn.execute("SELECT * FROM wallet_topups WHERE id=%s AND user_id=%s FOR UPDATE",(topup_id,user["id"])).fetchone()
            if not row or row["status"] not in ("awaiting_payment","manual_review","more_info"):
                raise HTTPException(409,"Receipt cannot be uploaded for this top-up")
            if row["receipt_path"] and row["status"]=="manual_review":
                raise HTTPException(409,"A receipt has already been submitted for review")
            name,target=_store_receipt(data)
            conn.execute("UPDATE wallet_topups SET receipt_path=%s,updated_at=now() WHERE id=%s",(name,topup_id))
            conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id,detail) VALUES (%s,'wallet_topup.receipt_uploaded','wallet_topup',%s,%s)",(user["id"],topup_id,json.dumps({"sha256":hashlib.sha256(data).hexdigest(),"size":len(data)})))
    except Exception:
        if target: target.unlink(missing_ok=True)
        raise
    return {"status":"manual_review","receipt_uploaded":True}

@router.get("/wallet/topups/{topup_id}")
def wallet_topup_status(topup_id:int,user=Depends(current_user)):
    with transaction() as conn:
        row=conn.execute("SELECT id,amount_minor,approved_amount_minor,status,customer_reference,receipt_path IS NOT NULL AS has_receipt,created_at,updated_at FROM wallet_topups WHERE id=%s AND user_id=%s",(topup_id,user["id"])).fetchone()
    if not row: raise HTTPException(404,"Top-up not found")
    return row

@router.get("/admin/wallet-topups")
def admin_wallet_topups(admin=Depends(require_operator)):
    with transaction() as conn:
        return conn.execute("""SELECT w.*,u.telegram_id,u.name,u.username,(w.receipt_path IS NOT NULL) AS has_receipt
          FROM wallet_topups w JOIN users u ON u.id=w.user_id ORDER BY w.id DESC LIMIT 100""").fetchall()

@router.get("/admin/wallet-topups/{topup_id}/receipt")
def admin_wallet_topup_receipt(topup_id:int,admin=Depends(require_operator)):
    with transaction() as conn:
        row=conn.execute("SELECT receipt_path FROM wallet_topups WHERE id=%s",(topup_id,)).fetchone()
    if not row or not row["receipt_path"] or Path(row["receipt_path"]).name!=row["receipt_path"]: raise HTTPException(404,"Receipt not found")
    target=_receipt_dir()/row["receipt_path"]
    if not target.is_file(): raise HTTPException(404,"Receipt not found")
    return FileResponse(target,media_type="application/octet-stream",filename=f"wallet-topup-{topup_id}-receipt",headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Content-Security-Policy":"default-src 'none'"})

@router.post("/admin/wallet-topups/{topup_id}/review")
def admin_wallet_topup_review(topup_id:int,body:WalletTopupReview,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        row=conn.execute("SELECT * FROM wallet_topups WHERE id=%s FOR UPDATE",(topup_id,)).fetchone()
        if not row or row["status"] not in ("manual_review","more_info"):
            raise HTTPException(409,"Top-up is not awaiting review")
        if body.action=="adjust":
            if not body.amount_minor: raise HTTPException(422,"A verified amount is required")
            conn.execute("UPDATE wallet_topups SET amount_minor=%s,updated_at=now() WHERE id=%s",(body.amount_minor,topup_id))
            result="manual_review"
        elif body.action=="approve":
            amount=body.amount_minor or row["amount_minor"]
            # Ledger reference is unique and core.ledger locks the wallet row, so retries cannot credit twice.
            from .core import ledger
            ledger(conn,row["user_id"],amount,"deposit",f"wallet-topup:{topup_id}")
            conn.execute("UPDATE wallet_topups SET approved_amount_minor=%s,status='approved',reviewed_by=%s,reviewed_at=now(),updated_at=now() WHERE id=%s",(amount,admin["id"],topup_id))
            conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)",(row["user_id"],"Your wallet top-up was approved."))
            result="approved"
        elif body.action=="reject":
            conn.execute("UPDATE wallet_topups SET status='rejected',reviewed_by=%s,reviewed_at=now(),updated_at=now() WHERE id=%s",(admin["id"],topup_id))
            conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)",(row["user_id"],"Your wallet top-up was rejected.")); result="rejected"
        else:
            conn.execute("UPDATE wallet_topups SET status='more_info',reviewed_by=%s,reviewed_at=now(),updated_at=now() WHERE id=%s",(admin["id"],topup_id))
            conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)",(row["user_id"],"More information is needed for your wallet top-up.")); result="more_info"
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id,detail) VALUES (%s,%s,'wallet_topup',%s,%s)",(admin["id"],"wallet_topup."+body.action,topup_id,json.dumps({"amount_minor":body.amount_minor,"note":body.note})))
    return {"status":result}

