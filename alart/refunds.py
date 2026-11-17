"""Full-order cancellation with durable, independently confirmed refund state.

Service mutations run under the worker's PostgreSQL advisory lock. External
refund attempts are committed before calling the provider: a crash or timeout
goes to reconciliation, never to an assumed success or a new payment attempt.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from .auth import current_user, require_operator, require_admin, csrf
from .db import transaction
from .payment_integrations import TelegramStars
from .sanaei import SanaeiProvider
from .provider import HTTPProvisioningProvider

router=APIRouter(prefix='/api')


class Cancellation(BaseModel):
    reason: str=Field(min_length=3,max_length=500)


class Decision(BaseModel):
    action: str=Field(pattern='^(approve|reject|confirm_manual|retry)$')
    reference: str=Field(default='',max_length=120)


def enqueue(conn, order_id, *, actor=None, reason='Provisioning failed', approved=False):
    # Lock order first across settlement, cancellation and review paths.
    order=conn.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(order_id,)).fetchone()
    if not order: raise HTTPException(404,'Order not found')
    existing=conn.execute('SELECT * FROM refund_requests WHERE order_id=%s FOR UPDATE',(order_id,)).fetchone()
    if existing: return existing
    if order['status']=='refunded': raise HTTPException(409,'Order is already refunded')
    payment=conn.execute('SELECT provider,status FROM payments WHERE order_id=%s FOR UPDATE',(order_id,)).fetchone()
    unpaid=order['status'] in ('awaiting_payment','rejected','expired') and payment['provider']!='manual'
    state='approved' if approved or unpaid else 'requested'
    if unpaid:
        conn.execute("UPDATE orders SET status='cancelled' WHERE id=%s",(order_id,))
        if order['renewal_of_sub_id'] and actor is not None:
            conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)",(order['user_id'],"Your renewal checkout was cancelled. Your existing service was not changed."))
    service_action='refund_only' if order['renewal_of_sub_id'] and order['status']!='active' else 'cancel'
    row=conn.execute("INSERT INTO refund_requests(order_id,requested_by,reason,status,service_action) VALUES (%s,%s,%s,%s,%s) RETURNING *",(order_id,actor,reason,state,service_action)).fetchone()
    conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,'cancellation.request','order',%s)",(actor,order_id))
    return row


@router.post('/orders/{order_id}/cancellation')
def request_cancellation(order_id:int,body:Cancellation,request:Request,user=Depends(current_user)):
    csrf(request)
    with transaction() as conn:
        if not conn.execute('SELECT id FROM orders WHERE id=%s AND user_id=%s FOR UPDATE',(order_id,user['id'])).fetchone():
            raise HTTPException(404,'Order not found')
        row=enqueue(conn,order_id,actor=user['id'],reason=body.reason)
    return {key:row[key] for key in ('id','order_id','status')}


@router.get('/admin/refunds')
def list_refunds(admin=Depends(require_admin)):
    with transaction() as conn:
        return conn.execute("""SELECT r.*,o.user_id,p.provider,p.amount_minor,p.currency,p.status AS payment_status
          FROM refund_requests r JOIN orders o ON o.id=r.order_id JOIN payments p ON p.order_id=o.id
          ORDER BY r.id DESC LIMIT 500""").fetchall()


def confirm(conn, row, source, reference=None):
    if row.get('confirmed_at'): return
    payment=conn.execute('SELECT * FROM payments WHERE order_id=%s FOR UPDATE',(row['order_id'],)).fetchone()
    if not payment or payment['status'] not in ('confirmed','refunded'):
        raise HTTPException(409,'Only a verified payment can be refunded')
    conn.execute("UPDATE payments SET status='refunded',updated_at=now() WHERE id=%s AND status<>'refunded'",(payment['id'],))
    conn.execute("UPDATE orders SET status='refunded' WHERE id=%s",(row['order_id'],))
    conn.execute("UPDATE refund_requests SET confirmed_at=now(),confirmation_source=%s,confirmation_reference=%s,status=CASE WHEN remote_disabled_at IS NULL THEN 'approved' ELSE 'completed' END,last_error=NULL,updated_at=now() WHERE id=%s",(source,reference,row['id']))
    from .referral import reverse_order
    reverse_order(conn,row['order_id'])
    conn.execute("INSERT INTO notifications(user_id,body) SELECT user_id,%s FROM orders WHERE id=%s",('Your refund is confirmed. Open Alart to view the status.',row['order_id']))
    conn.execute("INSERT INTO audit_logs(action,entity,entity_id) VALUES ('refund.confirmed','order',%s)",(row['order_id'],))


@router.post('/admin/refunds/{refund_id}/review')
def review_refund(refund_id:int,body:Decision,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        locator=conn.execute('SELECT order_id FROM refund_requests WHERE id=%s',(refund_id,)).fetchone()
        if not locator: raise HTTPException(404,'Refund request not found')
        conn.execute('SELECT id FROM orders WHERE id=%s FOR UPDATE',(locator['order_id'],))
        row=conn.execute('SELECT * FROM refund_requests WHERE id=%s FOR UPDATE',(refund_id,)).fetchone()
        if row['status']=='completed': return {'status':'completed'}
        if body.action in ('approve','reject'):
            target='approved' if body.action=='approve' else 'rejected'
            if row['status']==target: return {'status':target}
            if row['status']!='requested': raise HTTPException(409,'Refund request cannot be reviewed in this state')
            payment=conn.execute('SELECT provider,status FROM payments WHERE order_id=%s',(row['order_id'],)).fetchone()
            if target=='approved' and payment['provider']=='manual' and payment['status']=='manual_review':
                raise HTTPException(409,'Review the manual payment before approving cancellation')
            conn.execute('UPDATE refund_requests SET status=%s,reviewed_by=%s,updated_at=now() WHERE id=%s',(target,admin['id'],refund_id))
            conn.execute("INSERT INTO notifications(user_id,body) SELECT user_id,%s FROM orders WHERE id=%s",('Your cancellation request was approved.' if target=='approved' else 'Your cancellation request was rejected. Contact support for details.',row['order_id']))
        elif body.action=='retry':
            if row['status']!='manual_review' or row['confirmed_at'] or row['remote_disabled_at']:
                raise HTTPException(409,'Refund request cannot be retried in this state')
            # A remote-disable retry is safe. An uncertain money movement is
            # reconciled through a verified provider event or manual evidence.
            conn.execute("UPDATE refund_requests SET status='approved',run_after=now(),last_error=NULL,reviewed_by=%s,updated_at=now() WHERE id=%s",(admin['id'],refund_id))
            target='approved'
        else:
            payment=conn.execute('SELECT provider FROM payments WHERE order_id=%s',(row['order_id'],)).fetchone()
            if payment['provider'] not in ('manual','nowpayments') or row['status']!='manual_review' or not row['remote_disabled_at']:
                raise HTTPException(409,'Manual confirmation is unavailable for this refund')
            reference=body.reference.strip()
            if len(reference)<8: raise HTTPException(422,'Enter the verified refund transaction reference')
            if conn.execute("SELECT id FROM refund_requests WHERE confirmation_source='operator' AND confirmation_reference=%s",(reference,)).fetchone():
                raise HTTPException(409,'Refund reference already recorded')
            confirm(conn,row,'operator',reference)
            target='completed'
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,%s,'refund',%s)",(admin['id'],'refund.'+body.action,refund_id))
    return {'status':target}


def provider_refunded(conn, order_id, source, reference):
    """Only authenticated Telegram events or re-fetched signed NOWPayments IPN."""
    existing=conn.execute('SELECT * FROM refund_requests WHERE order_id=%s FOR UPDATE',(order_id,)).fetchone()
    if existing and existing['confirmed_at']:
        if existing['confirmation_source']!=source or existing['confirmation_reference']!=reference:
            raise HTTPException(409,'Refund reference does not match the order')
        return
    row=existing or enqueue(conn,order_id,reason='Provider confirmed refund',approved=True)
    confirm(conn,row,source,reference)


def process_one():
    # Called inside the same global service-operation lock as provisioning/expiry.
    with transaction() as conn:
        locator=conn.execute("""SELECT id,order_id FROM refund_requests WHERE status IN ('approved','refund_pending','processing')
          AND run_after<=now() ORDER BY id LIMIT 1""").fetchone()
        if not locator: return False
        conn.execute('SELECT id FROM orders WHERE id=%s FOR UPDATE',(locator['order_id'],))
        row=conn.execute('SELECT * FROM refund_requests WHERE id=%s FOR UPDATE',(locator['id'],)).fetchone()
        if row['status'] not in ('approved','refund_pending','processing'): return True
        if row['status']=='processing':
            conn.execute("UPDATE refund_requests SET status='manual_review',last_error='Refund outcome is uncertain; reconcile with provider',updated_at=now() WHERE id=%s",(row['id'],))
            return True
        data=conn.execute("""SELECT o.*,p.id AS payment_id,p.status AS payment_status,p.provider_reference,u.telegram_id
          FROM orders o JOIN payments p ON p.order_id=o.id JOIN users u ON u.id=o.user_id WHERE o.id=%s FOR UPDATE OF o,p""",(row['order_id'],)).fetchone()
        if not row['remote_disabled_at'] and row['service_action']=='refund_only':
            conn.execute("UPDATE renewal_jobs SET status='failed',lease_until=NULL WHERE order_id=%s AND status IN ('pending','retrying','processing')",(row['order_id'],))
            conn.execute("UPDATE orders SET status='cancelled' WHERE id=%s AND status<>'refunded'",(row['order_id'],))
            conn.execute("UPDATE refund_requests SET remote_disabled_at=now(),status='refund_pending',updated_at=now() WHERE id=%s",(row['id'],))
            row['remote_disabled_at']=True
        if not row['remote_disabled_at']:
            sub=conn.execute("""SELECT x.id,x.remote_id,c.client_email,s.api_url,s.api_token,s.api_token_ciphertext,
              mp.base_url,mp.credential_ciphertext FROM subscriptions x JOIN servers s ON s.id=x.server_id
              LEFT JOIN provisioned_configurations c ON c.subscription_id=x.id LEFT JOIN managed_panels mp ON mp.id=s.panel_id
              WHERE x.id=%s OR x.order_id=%s""",(data['renewal_of_sub_id'],row['order_id'])).fetchone()
            try:
                if sub and sub['client_email']: SanaeiProvider.from_panel(sub).disable_client(sub['client_email'])
                elif sub and sub['remote_id']: HTTPProvisioningProvider().disable_service(server=sub,remote_id=sub['remote_id'])
            except Exception:
                conn.execute("UPDATE refund_requests SET attempts=attempts+1,status=CASE WHEN attempts>=4 THEN 'manual_review' ELSE 'approved' END,last_error='Remote cancellation failed',run_after=now()+interval '1 minute',updated_at=now() WHERE id=%s",(row['id'],))
                return True
            if sub:
                conn.execute("UPDATE subscriptions SET status='cancelled',configuration=NULL WHERE id=%s",(sub['id'],))
                conn.execute("UPDATE provisioned_configurations SET status='disabled' WHERE subscription_id=%s",(sub['id'],))
                # Cancel queued operations for this service; their verified payments
                # enter the same refund workflow instead of being lost.
                other=conn.execute("SELECT order_id FROM renewal_jobs WHERE subscription_id=%s AND status IN ('pending','retrying','processing') AND order_id<>%s",(sub['id'],row['order_id'])).fetchall()
                for item in other: enqueue(conn,item['order_id'],reason='Service cancelled',approved=True)
                conn.execute("UPDATE renewal_jobs SET status='failed',lease_until=NULL WHERE subscription_id=%s AND status IN ('pending','retrying','processing')",(sub['id'],))
            conn.execute("UPDATE provisioning_jobs SET status='failed',lease_until=NULL WHERE order_id=%s AND status IN ('pending','retrying','processing')",(row['order_id'],))
            conn.execute("UPDATE orders SET status='cancelled' WHERE id=%s AND status<>'refunded'",(row['order_id'],))
            conn.execute("UPDATE refund_requests SET remote_disabled_at=now(),status='refund_pending',updated_at=now() WHERE id=%s",(row['id'],))
            row['remote_disabled_at']=True
            from .referral import reverse_order
            reverse_order(conn,row['order_id'])
            conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)",(data['user_id'],'Your service is cancelled. Refund status is shown separately in Alart.'))
        if row['confirmed_at']:
            conn.execute("UPDATE refund_requests SET status='completed',updated_at=now() WHERE id=%s",(row['id'],))
            return True
        if data['payment_status'] not in ('confirmed','refunded') or data['price_minor']==0:
            conn.execute("UPDATE refund_requests SET status='completed',confirmation_source='none',updated_at=now() WHERE id=%s",(row['id'],))
            return True
        if data['payment_status']=='refunded':
            conn.execute("UPDATE refund_requests SET status='manual_review',last_error='Existing provider refund needs reconciliation',updated_at=now() WHERE id=%s",(row['id'],))
            return True
        if data['payment_method']=='wallet':
            from .payment import WalletPaymentProvider
            WalletPaymentProvider().refund(conn,user_id=data['user_id'],amount_minor=data['price_minor'],reference=f"refund:payment:{data['payment_id']}")
            confirm(conn,row,'wallet',f"payment:{data['payment_id']}")
            return True
        if data['payment_method']!='stars' or not data['provider_reference']:
            conn.execute("UPDATE refund_requests SET status='manual_review',last_error='External refund requires verified manual review',updated_at=now() WHERE id=%s",(row['id'],))
            return True
        conn.execute("UPDATE refund_requests SET status='processing',updated_at=now() WHERE id=%s",(row['id'],))
    try:
        if TelegramStars().refund(telegram_id=data['telegram_id'],charge_id=data['provider_reference']) is not True:
            raise ValueError('Refund not confirmed')
    except Exception:
        with transaction() as conn:
            conn.execute("UPDATE refund_requests SET status='manual_review',last_error='Refund outcome is uncertain; reconcile with provider',updated_at=now() WHERE id=%s AND confirmed_at IS NULL",(row['id'],))
        return True
    with transaction() as conn:
        conn.execute('SELECT id FROM orders WHERE id=%s FOR UPDATE',(row['order_id'],))
        current=conn.execute('SELECT * FROM refund_requests WHERE id=%s FOR UPDATE',(row['id'],)).fetchone()
        confirm(conn,current,'telegram',data['provider_reference'])
    return True
