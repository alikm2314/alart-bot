"""Durable, credential-free operational events for private Telegram topics."""
import os
import logging
import httpx
from pathlib import Path
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from .db import transaction

log=logging.getLogger("alart.ops")
EVENTS={
    "purchase.created":("PURCHASES","Purchase created"),
    "payment.confirmed":("PAYMENTS","Payment confirmed"),
    "payment.manual_review":("PAYMENTS","Manual payment submitted for review"),
    "payment.manual_approved":("PAYMENTS","Manual payment approved"),
    "payment.manual_rejected":("PAYMENTS","Manual payment rejected"),
    "wallet.changed":("WALLET","Wallet ledger entry recorded"),
    "referral.awarded":("REFERRAL","Referral reward earned"),
    "referral.reversed":("REFERRAL","Referral reward reversal started"),
    "provisioning.active":("PROVISIONING","Service provisioned"),
    "provisioning.failed":("ERRORS","Provisioning failed after retries"),
    "renewal.active":("PROVISIONING","Service renewed"),
    "renewal.failed":("ERRORS","Renewal failed after retries"),
    "system.alert":("SYSTEM","System alert opened"),
    "security.admin_login":("SECURITY","Admin signed in"),
    "users.new":("USERS","User account active"),
    "backup.completed":("BACKUP","Encrypted backup completed"),
    "finance.report":("FINANCE","Finance report generated"),
}

TOPIC_TITLES={
    "PURCHASES":"🛒 Purchases", "PAYMENTS":"💳 Payments", "WALLET":"👛 Wallet",
    "REFERRAL":"🎁 Referrals", "USERS":"👤 Users", "PROVISIONING":"🔌 Provisioning",
    "ERRORS":"🚨 Errors", "SECURITY":"🛡️ Security", "BACKUP":"🗄️ Backups",
    "SYSTEM":"⚙️ System", "FINANCE":"📈 Finance",
}

def emit(conn,event_type:str,entity_id:int,*,key:str|None=None):
    if event_type not in EVENTS or type(entity_id) is not int or entity_id<1:
        raise ValueError("Invalid operational event")
    conn.execute("""INSERT INTO ops_events(event_type,entity_id,event_key) VALUES (%s,%s,%s)
      ON CONFLICT (event_key) DO NOTHING""",(event_type,entity_id,f"{event_type}:{key or entity_id}"))

def _destination(event_type:str):
    category=EVENTS[event_type][0]
    chat=os.environ.get("OPS_CHAT_ID","")
    topic=os.environ.get(f"OPS_TOPIC_{category}","")
    if chat.lstrip("-").isdigit() and topic.isdigit() and int(topic)>0:
        return int(chat),int(topic)
    # Topics created by the owner through the bot are durable operational
    # configuration, so updates do not require editing .env for every topic.
    try:
        with transaction() as conn:
            row=conn.execute("SELECT chat_id,topic_id FROM ops_topic_settings WHERE category=%s",(category,)).fetchone()
        if row: return int(row["chat_id"]),int(row["topic_id"])
    except Exception:
        log.warning("Could not read stored topic configuration")
    return None

def deliver(limit:int=10):
    with transaction() as conn:
        rows=conn.execute("""SELECT id,event_type,entity_id FROM ops_events WHERE delivered_at IS NULL
          AND next_attempt_at<=now() ORDER BY id LIMIT %s""",(limit,)).fetchall()
    sent=0
    for row in rows:
        destination=_destination(row["event_type"])
        if not destination:
            continue
        chat,topic=destination
        try:
            response=httpx.post(f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}/sendMessage",
                json={"chat_id":chat,"message_thread_id":topic,
                      "text":event_text(row['event_type'],row['entity_id'])},timeout=4)
            response.raise_for_status()
            if not response.json().get("ok"): raise ValueError("Telegram rejected operational event")
            with transaction() as conn:
                conn.execute("UPDATE ops_events SET delivered_at=now(),attempts=attempts+1 WHERE id=%s AND delivered_at IS NULL",(row["id"],))
            sent+=1
        except Exception:
            log.warning("Operational event %s delivery failed",row["id"])
            with transaction() as conn:
                conn.execute("UPDATE ops_events SET attempts=attempts+1,next_attempt_at=now()+interval '5 minutes' WHERE id=%s AND delivered_at IS NULL",(row["id"],))
    return sent

def _send(category:str,text:str,*,document:Path|None=None)->bool:
    destination=_destination_by_category(category)
    if not destination:
        return False
    chat,topic=destination
    try:
        if document:
            if not document.is_file() or document.suffix!='.enc' or document.stat().st_size>50*1024*1024:
                raise ValueError('Invalid backup archive')
            with document.open('rb') as stream:
                response=httpx.post(f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}/sendDocument",
                    data={'chat_id':str(chat),'message_thread_id':str(topic),'caption':text[:1000]},
                    files={'document':(document.name,stream,'application/octet-stream')},timeout=90)
        else:
            response=httpx.post(f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}/sendMessage",
                json={'chat_id':chat,'message_thread_id':topic,'text':text[:4000]},timeout=12)
        response.raise_for_status()
        return bool(response.json().get('ok'))
    except Exception:
        log.warning('Operational Telegram delivery failed for %s',category)
        return False

def _destination_by_category(category:str):
    chat=os.environ.get('OPS_CHAT_ID','')
    topic=os.environ.get(f'OPS_TOPIC_{category}','')
    if chat.lstrip('-').isdigit() and topic.isdigit() and int(topic)>0:
        return int(chat),int(topic)
    try:
        with transaction() as conn:
            row=conn.execute('SELECT chat_id,topic_id FROM ops_topic_settings WHERE category=%s',(category,)).fetchone()
        return (int(row['chat_id']),int(row['topic_id'])) if row else None
    except Exception:
        return None

def send_backup(path:str)->bool:
    archive=Path(path)
    return _send('BACKUP',f"🗄️ Encrypted database backup\n──────────\nFile: {archive.name}\nTime: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\nKeep the encryption key offline.",document=archive)

def deliver_daily_report(now:datetime|None=None)->bool:
    """Send one Tehran-day report after midnight, never fabricating metrics."""
    local=(now or datetime.now(timezone.utc)).astimezone(ZoneInfo('Asia/Tehran'))
    if local.hour!=0 or local.minute<5:
        return False
    report_day=local.date().fromordinal(local.date().toordinal()-1)
    if not _destination_by_category('FINANCE'):
        return False
    with transaction() as conn:
        existing=conn.execute('SELECT 1 FROM daily_operational_reports WHERE report_date=%s FOR UPDATE',(report_day,)).fetchone()
        if existing: return False
        start=report_day.isoformat(); end=report_day.fromordinal(report_day.toordinal()+1).isoformat()
        sales=conn.execute("SELECT count(*) n,COALESCE(sum(price_minor),0) amount FROM orders WHERE created_at>=%s::date AND created_at<%s::date AND status IN ('paid','provisioning','active')",(start,end)).fetchone()
        renewals=conn.execute("SELECT count(*) n FROM orders WHERE created_at>=%s::date AND created_at<%s::date AND renewal_of_sub_id IS NOT NULL AND status IN ('paid','provisioning','active')",(start,end)).fetchone()
        active=conn.execute("SELECT count(*) n FROM subscriptions WHERE created_at>=%s::date AND created_at<%s::date AND status='active'",(start,end)).fetchone()
        users=conn.execute("SELECT count(*) n FROM users WHERE created_at>=%s::date AND created_at<%s::date",(start,end)).fetchone()
        deposits=conn.execute("SELECT COALESCE(sum(amount_minor),0) amount FROM wallet_transactions WHERE created_at>=%s::date AND created_at<%s::date AND kind='deposit'",(start,end)).fetchone()
        text=(f"📈 Daily report · {report_day.isoformat()}\n──────────\n"
              f"Successful purchases: {sales['n']}\nRenewals: {renewals['n']}\nActivated services: {active['n']}\nNew users: {users['n']}\nSales total: {sales['amount']//10:,} Toman\nWallet deposits: {deposits['amount']//10:,} Toman")
    # Do not keep a database connection while posting to Telegram. The worker
    # intentionally has a very small connection pool for the target VPS.
    if not _send('FINANCE',text): return False
    with transaction() as conn:
        conn.execute('INSERT INTO daily_operational_reports(report_date) VALUES (%s) ON CONFLICT DO NOTHING',(report_day,))
    return True

def event_text(event_type:str,entity_id:int)->str:
    """Short, non-sensitive operational report text.

    The durable queue intentionally stores only controlled event type and ID;
    it never stores customer connection links, provider credentials, receipts,
    or arbitrary error payloads.
    """
    label=EVENTS[event_type][1]
    icons={"purchase.created":"🛒","payment.confirmed":"✅","payment.manual_review":"🧾",
        "payment.manual_approved":"✅","payment.manual_rejected":"❌","wallet.changed":"👛",
        "referral.awarded":"🎁","referral.reversed":"↩️","provisioning.active":"🔌",
        "provisioning.failed":"🚨","renewal.active":"🔄","renewal.failed":"🚨",
        "system.alert":"⚙️","security.admin_login":"🛡️","users.new":"👤",
        "backup.completed":"🗄️","finance.report":"📈"}
    lines=[f"{icons.get(event_type,'•')} {label}","──────────"]
    try:
        with transaction() as conn:
            if event_type in ('purchase.created','payment.confirmed','payment.manual_review','provisioning.active','renewal.active'):
                row=conn.execute("""SELECT o.id,p.name AS plan,u.name,u.username,u.telegram_id,o.price_minor,o.currency
                  FROM orders o JOIN users u ON u.id=o.user_id LEFT JOIN plans p ON p.id=o.plan_id WHERE o.id=%s""",(entity_id,)).fetchone()
                if row:
                    lines += [f"Order: #{row['id']} · {row['plan'] or 'Custom plan'}",f"Amount: {row['price_minor']//10:,} Toman",f"User: {row['name'] or '—'} · @{row['username'] or '—'}",f"Telegram ID: {row['telegram_id']}"]
            elif event_type=='wallet.changed':
                row=conn.execute("""SELECT u.name,u.username,u.telegram_id,w.balance_minor FROM users u
                  JOIN wallets w ON w.user_id=u.id WHERE u.id=%s""",(entity_id,)).fetchone()
                if row: lines += [f"User: {row['name'] or '—'} · @{row['username'] or '—'}",f"Telegram ID: {row['telegram_id']}",f"Current balance: {row['balance_minor']//10:,} Toman"]
            elif event_type=='users.new':
                row=conn.execute("SELECT name,username,telegram_id FROM users WHERE id=%s",(entity_id,)).fetchone()
                if row: lines += [f"Name: {row['name'] or '—'}",f"Username: @{row['username'] or '—'}",f"Telegram ID: {row['telegram_id']}"]
            else:
                lines.append(f"Reference: #{entity_id}")
    except Exception:
        # Reporting must never expose database errors or prevent a legitimate
        # operational event from being delivered.
        lines.append(f"Reference: #{entity_id}")
    lines.append(f"Time: {__import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)

if __name__=="__main__":
    import sys
    import time
    if len(sys.argv)==3 and sys.argv[1]=="backup":
        if not send_backup(sys.argv[2]):
            raise SystemExit('Backup Telegram delivery failed')
    else:
        raise SystemExit("Usage: python -m alart.ops_topics backup")
