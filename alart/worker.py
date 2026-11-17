import logging
import time
import httpx
from .db import transaction, migrate
from .core import activate
from .payment import WalletPaymentProvider
from .provider import HTTPProvisioningProvider
from .config import settings
from .sanaei import SanaeiProvider, PanelError, client_name, legacy_server_token, validate_panel_url, _check_public_dns
from .panel_management import check_and_discover

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("alart.worker")

def claim():
    with transaction() as conn:
        row = conn.execute("""SELECT j.* FROM provisioning_jobs j WHERE
          ((j.status IN ('pending','retrying') AND j.run_after<=now()) OR
          (j.status='processing' AND j.lease_until<now())) AND NOT EXISTS (SELECT 1 FROM refund_requests r WHERE r.order_id=j.order_id AND r.status NOT IN ('requested','rejected'))
          ORDER BY j.run_after,j.id FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if row:
            conn.execute("UPDATE provisioning_jobs SET status='processing',attempts=attempts+1,lease_until=now()+interval '2 minutes' WHERE id=%s", (row["id"],))
            conn.execute("UPDATE orders SET status='provisioning' WHERE id=%s AND status='paid'", (row["order_id"],))
        return row

def process_one():
    job = claim()
    if not job:
        return False
    try:
        with transaction() as conn:
            data = conn.execute("""SELECT p.*,o.selection,o.user_id,o.server_id,u.telegram_id,u.username,u.language,
              s.panel_id,mp.base_url,mp.credential_ciphertext,mp.enabled AS panel_enabled,mp.health AS panel_health
              FROM orders o JOIN plans p ON p.id=o.plan_id JOIN users u ON u.id=o.user_id
              JOIN servers s ON s.id=o.server_id JOIN managed_panels mp ON mp.id=s.panel_id WHERE o.id=%s""", (job["order_id"],)).fetchone()
            if not data or not data["panel_enabled"] or data["panel_health"]!="online":
                raise PanelError("Selected panel is unavailable")
            inbound_rows=conn.execute("""SELECT i.remote_id FROM panel_inbounds i JOIN service_inbound_mappings m ON m.inbound_id=i.id
              WHERE i.panel_id=%s AND m.service_id=%s AND i.allowed AND i.remote_enabled ORDER BY i.remote_id""",(data["panel_id"],data["service_id"])).fetchall()
            if not inbound_rows:
                raise PanelError("No allowed inbound remains for this product")
            inbound_ids=[row["remote_id"] for row in inbound_rows]
            duration_hours=data['selection'].get('duration_hours')
            if duration_hours is None:
                duration_hours=data["selection"].get("duration_days")*24 if data["is_custom"] and data["selection"].get("duration_days") is not None else data["duration_hours"]
            traffic_bytes=data["selection"].get("traffic_gb")*1024**3 if data["selection"].get("traffic_gb") else None
            existing=conn.execute("SELECT * FROM provisioned_configurations WHERE subscription_id=%s",(job["subscription_id"],)).fetchone()
            if existing:
                email=existing["client_email"]
                inbound_ids=list(existing["inbound_ids"])
                traffic_bytes=existing["traffic_limit_bytes"]
                expires_ms=int(existing["expires_at"].timestamp()*1000) if existing["expires_at"] else None
            else:
                expires_ms=int((time.time()+duration_hours*3600)*1000) if duration_hours else None
                count=conn.execute("""INSERT INTO user_configuration_counters(user_id,last_number) VALUES (%s,1)
                  ON CONFLICT (user_id) DO UPDATE SET last_number=user_configuration_counters.last_number+1
                  RETURNING last_number""",(data["user_id"],)).fetchone()["last_number"]
                email=client_name(data["username"],data["telegram_id"],data["user_id"],count)
                conn.execute("""INSERT INTO provisioned_configurations(user_id,subscription_id,order_id,panel_id,client_email,client_name,inbound_ids,user_limit,traffic_limit_bytes,expires_at,status)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s IS NULL THEN NULL ELSE to_timestamp(%s/1000.0) END,'pending')""",(data["user_id"],job["subscription_id"],job["order_id"],data["panel_id"],email,email,inbound_ids,data["selection"]["users"],traffic_bytes,expires_ms,expires_ms))
        provider=SanaeiProvider.from_panel(data)
        result=provider.create_client(email=email,telegram_id=data["telegram_id"],inbound_ids=inbound_ids,traffic_bytes=traffic_bytes,expires_at_ms=expires_ms,user_limit=data["selection"]["users"])
        with transaction() as conn:
            activate(conn,job,result["remote_id"],result["configuration"],expires_ms,traffic_bytes,data["selection"]["users"])
            conn.execute("UPDATE provisioned_configurations SET status='active',remote_client_id=%s WHERE subscription_id=%s",(result["remote_id"],job["subscription_id"]))
            subscription_url=result.get("subscription_url")
            if subscription_url:
                conn.execute("UPDATE subscriptions SET provider_subscription_url=%s WHERE id=%s",(subscription_url,job['subscription_id']))
            conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)", (data["user_id"],"Your service is active. Your subscription link and connection guidance are ready."))
        # Delivery is best-effort: Telegram errors never change provisioning state.
        try:
            from .bot import message, qr_photo
            from .qr import provider_subscription_png
            from datetime import datetime, timezone
            from .service_delivery import service_message
            sub={'id':job['subscription_id'],'name':data['name'],'client_name':email,'status':'active',
                 'expires_at':datetime.fromtimestamp(expires_ms/1000,timezone.utc) if expires_ms else None,
                 'traffic_limit_bytes':traffic_bytes,'traffic_used_bytes':0,'user_limit':data['selection']['users'],
                 'provider_subscription_url':subscription_url,'price_minor':data['price_minor']}
            text,markup=service_message(sub,data['language'])
            message(data['telegram_id'],text,markup,html=True)
            if subscription_url: qr_photo(data['telegram_id'],provider_subscription_png(subscription_url),'کد اشتراک شما؛ لینک در پیام بالا قابل کپی است.' if data['language']=='fa' else 'Your subscription QR. Copy the link from the message above.')
        except Exception:
            log.warning("Could not deliver service subscription for provisioning job %s",job["id"])
    except Exception as exc:
        log.error("Provisioning job %s failed: %s", job["id"],type(exc).__name__)
        safe_error=str(exc) if isinstance(exc,PanelError) else "Provisioning operation failed"
        with transaction() as conn:
            attempts = job["attempts"] + 1
            if attempts >= 5:
                conn.execute("UPDATE provisioning_jobs SET status='failed',last_error=%s,lease_until=NULL WHERE id=%s", (safe_error[:500],job["id"]))
                conn.execute("UPDATE subscriptions SET status='provisioning_failed' WHERE id=%s AND status='pending'", (job["subscription_id"],))
                order=conn.execute("UPDATE orders SET status='failed' WHERE id=%s RETURNING user_id,price_minor,payment_method", (job["order_id"],)).fetchone()
                if order:
                    from .refunds import enqueue
                    enqueue(conn,job['order_id'],approved=True)
                alert(conn,f"job:{job['id']}","critical","Provisioning failed")
                from .ops_topics import emit
                emit(conn,"provisioning.failed",job["id"])
                if order:
                    conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)",(order["user_id"],"We could not activate your service. Support has been alerted."))
            else:
                delay = min(3600, 2 ** attempts * 30)
                conn.execute("UPDATE provisioning_jobs SET status='retrying',last_error=%s,lease_until=NULL,run_after=now()+(%s || ' seconds')::interval WHERE id=%s", (safe_error[:500],delay,job["id"]))
    return True

def process_renewal():
    with transaction() as conn:
        job=conn.execute("""SELECT * FROM renewal_jobs j WHERE
          ((j.status IN ('pending','retrying') AND j.run_after<=now()) OR (j.status='processing' AND j.lease_until<now())) AND NOT EXISTS (SELECT 1 FROM refund_requests r WHERE r.order_id=j.order_id AND r.status NOT IN ('requested','rejected'))
          ORDER BY j.run_after,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1""").fetchone()
        if not job:
            return False
        conn.execute("UPDATE renewal_jobs SET status='processing',attempts=attempts+1,lease_until=now()+interval '2 minutes' WHERE id=%s",(job["id"],))
        conn.execute("UPDATE orders SET status='provisioning' WHERE id=%s AND status='paid'",(job["order_id"],))
    try:
        with transaction() as conn:
            data=conn.execute("""SELECT o.selection,o.user_id,o.payment_method,p.duration_hours,p.is_custom,svc.category,
              s.status AS subscription_status,s.expires_at,s.traffic_limit_bytes,s.user_limit,c.client_email,c.panel_id,
              mp.base_url,mp.credential_ciphertext
              FROM orders o JOIN plans p ON p.id=o.plan_id JOIN subscriptions s ON s.id=%s
              JOIN services svc ON svc.id=p.service_id
              JOIN provisioned_configurations c ON c.subscription_id=s.id
              JOIN managed_panels mp ON mp.id=c.panel_id WHERE o.id=%s FOR UPDATE OF s""",(job["subscription_id"],job["order_id"])).fetchone()
            if not data or data['subscription_status'] not in ('active','expiring','expired'):
                raise PanelError("Existing configuration is unavailable for renewal")
            if job['prepared_at'] is None:
                from .renewal import project
                duration=data['selection'].get('duration_hours')
                if duration is None:
                    duration=data["selection"].get("duration_days")*24 if data["is_custom"] and data["selection"].get("duration_days") is not None else data["duration_hours"]
                details=project(category=data['category'],status=data['subscription_status'],expires_at=data['expires_at'],
                    traffic_limit_bytes=data['traffic_limit_bytes'],duration_hours=duration,
                    traffic_gb=data['selection'].get('traffic_gb'))
                conn.execute("""UPDATE renewal_jobs SET prepared_at=now(),previous_expires_at=%s,target_expires_at=%s,
                  previous_traffic_limit_bytes=%s,target_traffic_limit_bytes=%s,added_traffic_bytes=%s,
                  duration_hours=%s,renewal_mode=%s WHERE id=%s AND prepared_at IS NULL""",
                  (details['previous_expires_at'],details['target_expires_at'],details['previous_traffic_limit_bytes'],
                   details['target_traffic_limit_bytes'],details['added_traffic_bytes'],details['duration_hours'],details['mode'],job['id']))
                job={**job,'prepared_at':True,'target_expires_at':details['target_expires_at'],
                     'target_traffic_limit_bytes':details['target_traffic_limit_bytes']}
        expiry_ms=int(job['target_expires_at'].timestamp()*1000) if job['target_expires_at'] else None
        total_bytes=job['target_traffic_limit_bytes']
        result=SanaeiProvider.from_panel(data).renew_client(data["client_email"],traffic_bytes=total_bytes,expires_at_ms=expiry_ms,user_limit=data['user_limit'])
        with transaction() as conn:
            conn.execute("UPDATE subscriptions SET configuration=%s,expires_at=CASE WHEN %s IS NULL THEN NULL ELSE to_timestamp(%s/1000.0) END,traffic_limit_bytes=%s,status='active',renewal_reminded_for=NULL,ended_at=NULL,archived_at=NULL,remote_missing_at=NULL WHERE id=%s AND status IN ('active','expiring','expired')",(result["configuration"],expiry_ms,expiry_ms,total_bytes,job["subscription_id"]))
            conn.execute("UPDATE provisioned_configurations SET expires_at=CASE WHEN %s IS NULL THEN NULL ELSE to_timestamp(%s/1000.0) END,traffic_limit_bytes=%s,status='active' WHERE subscription_id=%s",(expiry_ms,expiry_ms,total_bytes,job["subscription_id"]))
            conn.execute("UPDATE subscriptions SET provider_subscription_url=%s WHERE id=%s",(result.get("subscription_url"),job["subscription_id"]))
            conn.execute("UPDATE orders SET status='active' WHERE id=%s",(job["order_id"],))
            conn.execute("UPDATE renewal_jobs SET status='succeeded',lease_until=NULL WHERE id=%s",(job["id"],))
            conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)",(data["user_id"],"Your service renewal is active."))
            conn.execute("INSERT INTO audit_logs(action,entity,entity_id) VALUES ('renewal.succeeded','order',%s)",(job['order_id'],))
            from .ops_topics import emit
            emit(conn,"renewal.active",job["order_id"])
    except Exception as exc:
        log.error("Renewal job %s failed: %s",job["id"],type(exc).__name__)
        safe=str(exc) if isinstance(exc,PanelError) else "Renewal failed"
        with transaction() as conn:
            attempts=job["attempts"]+1
            if attempts>=5:
                conn.execute("UPDATE renewal_jobs SET status='failed',lease_until=NULL,last_error=%s WHERE id=%s",(safe,job["id"]))
                order=conn.execute("UPDATE orders SET status='failed' WHERE id=%s RETURNING user_id,price_minor,payment_method",(job["order_id"],)).fetchone()
                from .refunds import enqueue
                # A timeout can mean the panel applied the update before our
                # response was lost. Require operator reconciliation before
                # money moves; never disable the existing client here.
                enqueue(conn,job['order_id'],reason='Renewal failed; reconcile panel state before refunding',approved=False)
                if order:
                    conn.execute("INSERT INTO notifications(user_id,body) VALUES (%s,%s)",(order['user_id'],"We could not confirm your renewal. Support is reviewing the panel and payment status."))
                alert(conn,f"renewal:{job['id']}","critical","Renewal failed")
                from .ops_topics import emit
                emit(conn,"renewal.failed",job["id"])
            else:
                delay=min(3600,2**attempts*30)
                conn.execute("UPDATE renewal_jobs SET status='retrying',lease_until=NULL,last_error=%s,run_after=now()+(%s || ' seconds')::interval WHERE id=%s",(safe,delay,job["id"]))
    return True

def alert(conn,key,severity,title):
    row = conn.execute("INSERT INTO alerts(dedupe_key,severity,title) VALUES (%s,%s,%s) ON CONFLICT (dedupe_key) WHERE status<>'resolved' DO UPDATE SET severity=EXCLUDED.severity RETURNING id", (key,severity,title)).fetchone()
    from .ops_topics import emit
    emit(conn,"system.alert",row["id"])
    return row["id"]

def monitor():
    with transaction() as conn:
        panels=conn.execute("SELECT id,base_url,credential_ciphertext FROM managed_panels WHERE enabled").fetchall()
    for panel in panels:
        result=check_and_discover(panel["id"])
        with transaction() as conn:
            key=f"panel:{panel['id']}:health"
            if result["health"]!="online":
                alert(conn,key,"critical","Sanaei panel unavailable")
            else:
                conn.execute("UPDATE alerts SET status='resolved',resolved_at=now() WHERE dedupe_key=%s AND status<>'resolved'",(key,))
        if result['health']=='online':
            try:
                metrics=SanaeiProvider.from_panel(panel).server_status()
                with transaction() as conn:
                    server=conn.execute("SELECT id FROM servers WHERE panel_id=%s",(panel['id'],)).fetchone()
                    if server:
                        health='healthy' if any(metrics[key] is not None for key in ('cpu_percent','ram_percent','swap_percent','disk_percent')) else 'unavailable'
                        conn.execute("""INSERT INTO server_metrics(server_id,cpu_percent,ram_percent,swap_percent,disk_percent,uptime_seconds,health)
                          VALUES (%s,%s,%s,%s,%s,%s,%s)""",(server['id'],metrics['cpu_percent'],metrics['ram_percent'],metrics['swap_percent'],metrics['disk_percent'],metrics['uptime_seconds'],health))
            except Exception:
                log.warning('Sanaei metrics unavailable for panel %s',panel['id'])
    with transaction() as conn:
        servers = conn.execute("SELECT * FROM servers WHERE status='enabled' AND panel_id IS NULL").fetchall()
    for server in servers:
        try:
            validate_panel_url(server["api_url"])
            _check_public_dns(httpx.URL(server["api_url"]).host)
            with httpx.Client(timeout=10,follow_redirects=False,trust_env=False) as client:
                response = client.get(server["api_url"].rstrip("/") + "/metrics",headers={"Authorization":"Bearer " + legacy_server_token(server)})
                response.raise_for_status()
                metrics = response.json()
            values = [metrics.get(x) for x in ("cpu_percent","ram_percent","swap_percent","disk_percent")]
            if any(v is not None and (not isinstance(v,(int,float)) or v<0 or v>100) for v in values):
                raise ValueError("Invalid metrics")
            health = "critical" if any(v is not None and v>=95 for v in values) else "warning" if any(v is not None and v>=85 for v in values) else "healthy"
        except Exception:
            log.error("Monitoring server %s failed",server["id"])
            metrics,values,health = {},[None]*4,"unavailable"
        with transaction() as conn:
            conn.execute("INSERT INTO server_metrics(server_id,cpu_percent,ram_percent,swap_percent,disk_percent,uptime_seconds,health) VALUES (%s,%s,%s,%s,%s,%s,%s)",(server["id"],*values,metrics.get("uptime_seconds"),health))
            key=f"server:{server['id']}:health"
            if health in ("critical","unavailable"):
                alert(conn,key,"critical",f"Server {server['name']} {health}")
            else:
                conn.execute("UPDATE alerts SET status='resolved',resolved_at=now() WHERE dedupe_key=%s AND status<>'resolved'",(key,))

def expire_remote_services():
    with transaction() as conn:
        rows=conn.execute("""SELECT x.id,x.user_id,x.remote_id,s.api_url,s.api_token,s.api_token_ciphertext,c.client_email,p.base_url,p.credential_ciphertext FROM subscriptions x
          JOIN servers s ON s.id=x.server_id LEFT JOIN provisioned_configurations c ON c.subscription_id=x.id
          LEFT JOIN managed_panels p ON p.id=s.panel_id WHERE x.status IN ('active','expiring') AND x.expires_at<=now()
          ORDER BY x.id LIMIT 100""").fetchall()
    for row in rows:
        try:
            if row["client_email"]:
                SanaeiProvider.from_panel(row).disable_client(row["client_email"])
            else:
                HTTPProvisioningProvider().disable_service(server=row,remote_id=row["remote_id"])
            with transaction() as conn:
                conn.execute("UPDATE subscriptions SET status='expired',configuration=NULL WHERE id=%s AND status IN ('active','expiring')",(row["id"],))
                conn.execute("UPDATE provisioned_configurations SET status='expired' WHERE subscription_id=%s",(row["id"],))

        except Exception:
            log.error("Could not disable expired subscription %s",row["id"])
            with transaction() as conn:
                alert(conn,f"expiry:{row['id']}","critical",f"Subscription {row['id']} could not be disabled")

def expire_depleted_services():
    with transaction() as conn:
        rows=conn.execute("""SELECT c.subscription_id,c.client_email,c.traffic_limit_bytes,s.user_id,p.base_url,p.credential_ciphertext
          FROM provisioned_configurations c JOIN subscriptions s ON s.id=c.subscription_id
          JOIN managed_panels p ON p.id=c.panel_id
          WHERE c.status='active' AND c.traffic_limit_bytes>0 AND s.status='active'
          AND (s.traffic_check_at IS NULL OR s.traffic_check_at<now()-interval '5 minutes')
          ORDER BY s.traffic_check_at NULLS FIRST,s.id LIMIT 10""").fetchall()
    for row in rows:
        try:
            with transaction() as conn:
                conn.execute("UPDATE subscriptions SET traffic_check_at=now() WHERE id=%s",(row["subscription_id"],))
            provider=SanaeiProvider.from_panel(row)
            traffic=provider.get_traffic(row["client_email"])
            used=traffic["used"]
            with transaction() as conn:
                conn.execute("UPDATE subscriptions SET traffic_used_bytes=%s,traffic_sampled_at=now() WHERE id=%s",(used,row["subscription_id"]))
            if used<row["traffic_limit_bytes"]:
                continue
            provider.disable_client(row["client_email"])
            with transaction() as conn:
                conn.execute("UPDATE subscriptions SET status='expired',configuration=NULL WHERE id=%s AND status='active'",(row["subscription_id"],))
                conn.execute("UPDATE provisioned_configurations SET status='expired' WHERE subscription_id=%s",(row["subscription_id"],))

        except Exception:
            log.error("Traffic check failed for subscription %s",row["subscription_id"])

def deliver_notifications():
    from .bot import message
    with transaction() as conn:
        rows=conn.execute("SELECT n.id,n.body,n.action_path,u.telegram_id,u.language FROM notifications n JOIN users u ON u.id=n.user_id WHERE n.delivered_at IS NULL ORDER BY n.id LIMIT 20").fetchall()
    for row in rows:
        try:
            from .localization import tr
            markup=None
            if row['action_path']:
                from .config import settings
                from .bot import renewal_button
                markup=renewal_button(row['action_path'],row['language'])
            message(row["telegram_id"],tr(row["language"],row["body"]),markup)
            with transaction() as conn:
                conn.execute("UPDATE notifications SET delivered_at=now() WHERE id=%s AND delivered_at IS NULL",(row["id"],))
        except Exception:
            log.error("Notification %s delivery failed",row["id"])

def deliver_broadcasts():
    """Send a small batch per maintenance cycle; broadcasts never block service provisioning."""
    from .bot import message
    with transaction() as conn:
        rows=conn.execute("""SELECT r.job_id,r.user_id,j.body,j.buttons,u.telegram_id FROM broadcast_recipients r
          JOIN broadcast_jobs j ON j.id=r.job_id JOIN users u ON u.id=r.user_id
          WHERE r.status='pending' ORDER BY r.job_id,r.user_id LIMIT 10 FOR UPDATE OF r SKIP LOCKED""").fetchall()
    for row in rows:
        try:
            from .message_actions import markup
            message(row['telegram_id'],row['body'],markup(row['buttons']))
            with transaction() as conn:
                conn.execute("UPDATE broadcast_recipients SET status='sent',sent_at=now() WHERE job_id=%s AND user_id=%s AND status='pending'",(row['job_id'],row['user_id']))
        except Exception:
            with transaction() as conn:
                conn.execute("UPDATE broadcast_recipients SET status='failed',last_error='Telegram delivery failed' WHERE job_id=%s AND user_id=%s AND status='pending'",(row['job_id'],row['user_id']))
    with transaction() as conn:
        conn.execute("""UPDATE broadcast_jobs j SET completed_at=now() WHERE completed_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM broadcast_recipients r WHERE r.job_id=j.id AND r.status='pending')""")

def remind_renewals():
    with transaction() as conn:
        rows=conn.execute("""WITH due AS (
          SELECT id FROM subscriptions WHERE status IN ('active','expiring')
          AND expires_at>now() AND expires_at<=now()+interval '5 days'
          AND renewal_reminded_for IS DISTINCT FROM expires_at
          ORDER BY expires_at,id LIMIT 20 FOR UPDATE SKIP LOCKED)
          UPDATE subscriptions s SET renewal_reminded_for=s.expires_at FROM due WHERE s.id=due.id
          RETURNING s.id,s.user_id,s.expires_at""").fetchall()
        for row in rows:
            # The timestamp is stable even if notification delivery is delayed.
            conn.execute("INSERT INTO notifications(user_id,body,action_path) VALUES (%s,%s,%s)",
                (row['user_id'],f"Your service expires on {row['expires_at'].strftime('%Y-%m-%d %H:%M UTC')}. Renew it in Alart.",f"/app/?renew={row['id']}"))

def main():
    settings()
    migrate()
    import threading
    from .bot import poll
    threading.Thread(target=poll,daemon=True,name="telegram-bot").start()
    last_maintenance=0
    while True:
        try:
            # Serialize all remote service mutations across worker instances.
            # The second pool connection remains available for each transaction.
            with transaction() as guard:
                locked=guard.execute('SELECT pg_try_advisory_xact_lock(9137013) AS locked').fetchone()['locked']
                if not locked:
                    time.sleep(2)
                    continue
                worked=process_one()
                renewed=process_renewal()
                from .refunds import process_one as process_refund
                worked=process_refund() or worked
                if time.monotonic()-last_maintenance>60:
                    from .lifecycle import reconcile, reminders
                    reconcile()
                    expire_remote_services()
                    expire_depleted_services()
                    reminders()
                    monitor()
                    from .stars_pricing import refresh_stored_reference
                    refresh_stored_reference()
                    deliver_notifications()
                    deliver_broadcasts()
                    from .ops_topics import deliver as deliver_ops_events
                    deliver_ops_events()
                    from .ops_topics import deliver_daily_report
                    deliver_daily_report()
                    from .referral import retry_pending_reversals
                    with transaction() as conn:
                        retry_pending_reversals(conn)
                    last_maintenance=time.monotonic()
            if not worked and not renewed:
                time.sleep(2)
        except Exception as exc:
            log.error("Worker loop failed: %s",type(exc).__name__)
            time.sleep(5)

if __name__=="__main__":
    main()
