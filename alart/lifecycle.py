"""Bounded remote reconciliation and durable, deduplicated customer notices."""
import logging
from datetime import datetime, timezone
from .db import transaction
from .sanaei import SanaeiProvider, PanelError
from .service_state import effective_status

log=logging.getLogger(__name__)


def reconcile():
    # One list call per panel, not one remote call per service on Mini App boot.
    with transaction() as conn:
        panels=conn.execute("""SELECT DISTINCT p.id,p.base_url,p.credential_ciphertext
          FROM managed_panels p JOIN provisioned_configurations c ON c.panel_id=p.id
          JOIN subscriptions s ON s.id=c.subscription_id WHERE p.enabled AND s.archived_at IS NULL
          AND (s.remote_checked_at IS NULL OR s.remote_checked_at<now()-interval '5 minutes') LIMIT 10""").fetchall()
    for panel in panels:
        try:
            provider=SanaeiProvider.from_panel(panel)
            remote={row['email']:row for row in provider.list_clients()}
            with transaction() as conn:
                rows=conn.execute("""SELECT s.*,c.client_email FROM subscriptions s
                  JOIN provisioned_configurations c ON c.subscription_id=s.id
                  WHERE c.panel_id=%s AND s.archived_at IS NULL AND s.status<>'pending'""",(panel['id'],)).fetchall()
            refreshed=0
            for row in rows:
                found=remote.get(row['client_email'])
                with transaction() as conn:
                    # Never overwrite a concurrently queued/running renewal.
                    locked=conn.execute("""SELECT s.id FROM subscriptions s WHERE s.id=%s
                      AND NOT EXISTS (SELECT 1 FROM renewal_jobs j WHERE j.subscription_id=s.id
                        AND j.status IN ('pending','processing','retrying')) FOR UPDATE""",(row['id'],)).fetchone()
                    if not locked: continue
                    if found is None:
                        conn.execute("""UPDATE subscriptions SET remote_missing_at=COALESCE(remote_missing_at,now()),remote_checked_at=now(),
                          status=CASE WHEN status IN ('active','expiring') THEN 'expired' ELSE status END,
                          ended_at=COALESCE(ended_at,now()),configuration=NULL,provider_subscription_url=NULL WHERE id=%s""",(row['id'],))
                        conn.execute("UPDATE provisioned_configurations SET status='expired' WHERE subscription_id=%s AND status='active'",(row['id'],))
                        continue
                    up,down=found['up_bytes'],found['down_bytes']
                    used=up+down if up is not None and down is not None else None
                    conn.execute("""UPDATE subscriptions SET remote_checked_at=now(),remote_missing_at=NULL,
                      traffic_used_bytes=COALESCE(%s,traffic_used_bytes),
                      traffic_sampled_at=CASE WHEN %s IS NULL THEN traffic_sampled_at ELSE now() END WHERE id=%s""",(used,used,row['id']))
                if row['status'] in ('active','expiring') and refreshed<5 and (not row.get('links_checked_at') or not row.get('provider_subscription_url')):
                    refreshed+=1
                    try:
                        client=provider.get_client(row['client_email'])
                        if not client: continue
                        result=provider._client_result(row['client_email'],client)
                        with transaction() as conn:
                            conn.execute("UPDATE subscriptions SET configuration=%s,provider_subscription_url=%s,links_checked_at=now() WHERE id=%s AND status IN ('active','expiring')",(result['configuration'],result['subscription_url'],row['id']))
                    except PanelError:
                        log.warning('Could not refresh subscription links for %s',row['id'])
        except (PanelError,RuntimeError):
            # A failed/unauthorized list is not proof that any client was deleted.
            log.warning('Panel reconciliation unavailable for %s',panel['id'])
    with transaction() as conn:
        conn.execute("""UPDATE subscriptions SET ended_at=COALESCE(ended_at,expires_at,now()) WHERE status='expired' AND ended_at IS NULL""")
        conn.execute("""UPDATE subscriptions s SET archived_at=now() FROM orders o JOIN plans p ON p.id=o.plan_id
          WHERE s.order_id=o.id AND p.price_minor=0 AND s.status IN ('expired','cancelled') AND s.archived_at IS NULL
          AND COALESCE(s.ended_at,s.expires_at,s.created_at)<now()-interval '2 days'""")


def notice_for(row, now=None):
    now=now or datetime.now(timezone.utc)
    fa=row['language']=='fa'
    status=effective_status(row,now)
    sid=row['id']
    title=f"سرویس #{sid}" if fa else f"Service #{sid}"
    cycle=f"{row.get('expires_at')}:{row.get('traffic_limit_bytes')}"
    if status=='expired':
        reason=('سرویس در پنل موجود نیست.' if fa else 'This service is no longer in the panel.') if row.get('remote_missing_at') else ('اعتبار یا حجم سرویس شما تمام شده است.' if fa else 'Your service time or traffic allowance has ended.')
        return f'end:{sid}:{cycle}',f'{title}\n🔴 {reason}'
    if status not in ('active','expiring'): return None
    expiry=row.get('expires_at')
    if expiry:
        seconds=(expiry-now).total_seconds()
        threshold=1 if seconds<=3600 else 24 if seconds<=86400 else 168 if seconds<=604800 else None
        if threshold:
            hours=max(1,int(seconds/3600))
            amount=f'{hours//24} روز' if hours>=24 else f'{hours} ساعت'
            en=f'{hours//24} days' if hours>=24 else f'{hours} hours'
            # Separate thresholds can be delivered on subsequent maintenance cycles.
            key=f'time:{sid}:{cycle}:{threshold}'
            body=f'{title}\n⏳ حدود {amount} تا پایان اعتبار باقی مانده است.' if fa else f'{title}\n⏳ About {en} remaining.'
            return key,body
    return None


def reminders():
    with transaction() as conn:
        rows=conn.execute("""SELECT s.*,u.language,p.price_minor=0 AS is_trial FROM subscriptions s
          JOIN users u ON u.id=s.user_id JOIN orders o ON o.id=s.order_id JOIN plans p ON p.id=o.plan_id
          WHERE s.archived_at IS NULL AND s.status IN ('active','expiring','expired') ORDER BY s.id""").fetchall()
        for row in rows:
            notices=[]
            notice=notice_for(row)
            if notice: notices.append(notice)
            limit,used=row.get('traffic_limit_bytes'),row.get('traffic_used_bytes')
            if effective_status(row) in ('active','expiring') and limit and used is not None and 0<limit-used<=min(1024**3,limit*.2):
                mb=max(1,round((limit-used)/1024**2))
                body=f"سرویس #{row['id']}\n🟡 فقط {mb} مگابایت از حجم شما باقی مانده است." if row['language']=='fa' else f"Service #{row['id']}\n🟡 Only {mb} MB remaining."
                notices.append((f"traffic:{row['id']}:{limit}",body))
            path='/app/?page=notifications' if row['is_trial'] or row.get('remote_missing_at') else f"/app/?renew={row['id']}"
            for key,body in notices:
                conn.execute("""INSERT INTO notifications(user_id,body,action_path,event_key) VALUES (%s,%s,%s,%s)
                  ON CONFLICT (event_key) WHERE event_key IS NOT NULL DO NOTHING""",(row['user_id'],body,path,key))
