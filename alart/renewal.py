"""Quotes and immutable targets for renewals of an existing provider client."""
import json
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from .pricing import quote as price_quote

GB=1024**3


def selection_for(original, candidate):
    if candidate['service_id']!=original['service_id'] or candidate['category']!=original['category']:
        raise HTTPException(409,'Renewal plan does not match this service')
    selected=original['selection'] if isinstance(original['selection'],dict) else json.loads(original['selection'])
    if candidate['is_custom']:
        if candidate['id']!=original['plan_id']:
            raise HTTPException(409,'Custom renewal plan must match the existing plan')
        return {key:selected.get(key) for key in ('users','traffic_gb','duration_days')}
    if candidate['user_limit']!=original['user_limit']:
        raise HTTPException(409,'Renewal cannot change the concurrent IP limit')
    return {'users':None,'traffic_gb':None,'duration_days':None}


def mode_for(category, duration_hours, added_traffic_bytes, current_traffic_bytes):
    if category=='volume_unlimited':
        if duration_hours or added_traffic_bytes<=0 or not current_traffic_bytes:
            raise HTTPException(409,'Traffic top-up is unavailable for this service')
        return 'traffic_topup'
    if duration_hours<=0:
        raise HTTPException(409,'This subscription has no renewable time period')
    if category=='volume_timed' and (added_traffic_bytes<=0 or not current_traffic_bytes):
        raise HTTPException(409,'Timed volume renewal requires a traffic allowance')
    return 'time_and_traffic' if added_traffic_bytes else 'time'


def project(*, category, status, expires_at, traffic_limit_bytes, duration_hours, traffic_gb, now=None):
    now=now or datetime.now(timezone.utc)
    added=(traffic_gb or 0)*GB
    mode=mode_for(category,duration_hours,added,traffic_limit_bytes)
    if mode!='traffic_topup' and expires_at is None:
        raise HTTPException(409,'This subscription has no expiration date to extend')
    if mode=='traffic_topup':
        new_expiry=None
    else:
        base=expires_at if status in ('active','expiring') and expires_at and expires_at>now else now
        new_expiry=base+timedelta(hours=duration_hours)
    if added and traffic_limit_bytes:
        target_traffic=traffic_limit_bytes+added
    else:
        target_traffic=traffic_limit_bytes
    return {'mode':mode,'previous_expires_at':expires_at,'target_expires_at':new_expiry,
            'previous_traffic_limit_bytes':traffic_limit_bytes,'target_traffic_limit_bytes':target_traffic,
            'added_traffic_bytes':added,'duration_hours':duration_hours}


def eligible(conn, user_id, subscription_id, *, lock=False):
    suffix=' FOR UPDATE OF s' if lock else ''
    row=conn.execute("""SELECT s.*,o.plan_id,o.selection,p.service_id,svc.category,c.client_email
      FROM subscriptions s JOIN orders o ON o.id=s.order_id JOIN plans p ON p.id=o.plan_id
      JOIN services svc ON svc.id=p.service_id JOIN provisioned_configurations c ON c.subscription_id=s.id
      WHERE s.id=%s AND s.user_id=%s AND s.status IN ('active','expiring','expired')"""+suffix,(subscription_id,user_id)).fetchone()
    if not row or not row['client_email']:
        raise HTTPException(404,'Renewable subscription not found')
    if row.get('remote_missing_at'):
        raise HTTPException(409,'This service was removed from the panel. Please contact support.')
    from .service_state import effective_status
    row['status']=effective_status(row)
    return row


def offer(conn, original, candidate, payment_method, *, now=None):
    chosen=selection_for(original,candidate)
    priced=price_quote(conn,plan_id=candidate['id'],payment_method=payment_method,**chosen)
    if priced.final_minor<=0:
        raise HTTPException(409,'Zero-value plans cannot be renewed through checkout')
    duration=(chosen['duration_days']*24 if candidate['is_custom'] and chosen['duration_days'] is not None else candidate['duration_hours'])
    details=project(category=original['category'],status=original['status'],expires_at=original['expires_at'],
                    traffic_limit_bytes=original['traffic_limit_bytes'],duration_hours=duration,
                    traffic_gb=priced.traffic_gb,now=now)
    # Keep the purchased limits on the order. Admin plan edits after checkout
    # must not silently change the service that a verified payment delivers.
    selection={**priced.selection,'duration_hours':duration}
    return {'plan_id':candidate['id'],'plan_name':candidate['name'],'price_minor':priced.final_minor,
            'currency':priced.currency,'selection':selection,**details}


def options(conn, user_id, subscription_id, payment_method):
    original=eligible(conn,user_id,subscription_id)
    rows=conn.execute("""SELECT p.id,p.name,p.service_id,p.is_custom,p.user_limit,p.duration_hours,p.traffic_gb,s.category
      FROM plans p JOIN services s ON s.id=p.service_id WHERE p.service_id=%s AND p.enabled AND s.enabled
      ORDER BY p.duration_hours,p.id""",(original['service_id'],)).fetchall()
    result=[]
    for candidate in rows:
        try: result.append(offer(conn,original,candidate,payment_method))
        except HTTPException: continue
    if not result: raise HTTPException(409,'No compatible renewal options are available')
    return {'subscription_id':subscription_id,'status':original['status'],'current_expires_at':original['expires_at'],
            'current_traffic_limit_bytes':original['traffic_limit_bytes'],'options':result}
