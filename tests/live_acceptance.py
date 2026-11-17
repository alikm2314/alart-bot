"""Explicitly invoked acceptance run: fresh QA database, real QA-only panel clients.

Run inside a configured container with ALART_RUN_LIVE_QA=1. Production business
tables are never written. Telegram delivery is captured, no money is charged.
"""
import os
import sys
import time
import json
import secrets
from pathlib import Path
from unittest.mock import patch
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from urllib.parse import urlsplit, urlunsplit

if os.environ.get('ALART_RUN_LIVE_QA')!='1':
    raise SystemExit('Explicit ALART_RUN_LIVE_QA=1 is required')
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root));os.chdir(root)
production=os.environ['DATABASE_URL']
db_name='alart_qa_'+time.strftime('%Y%m%d_%H%M%S')
parts=urlsplit(production)
qa_url=urlunsplit((parts.scheme,parts.netloc,'/'+db_name,parts.query,''))
copied={}
tables=('locations','managed_panels','servers','services','plans','pricing_rules','panel_inbounds','service_inbound_mappings','payment_settings','custom_volume_offers')
with psycopg.connect(production,autocommit=True,row_factory=dict_row) as source:
    for table in tables:
        copied[table]=source.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table))).fetchall()
    source.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(db_name)))
os.environ['DATABASE_URL']=qa_url
os.environ['PRIVATE_DATA_DIR']=str(root/'qa-private')
from alart.db import transaction, migrate, pool
from alart import core, worker, lifecycle
from alart.sanaei import SanaeiProvider
from alart.api import app
from alart.auth import current_user, require_owner
from fastapi.testclient import TestClient

created=[]
results=[]
def passed(label):
    results.append(label);print('PASS',label,flush=True)

try:
    migrate();migrate();passed('all migrations apply and are repeatable')
    with transaction() as conn:
        for table in tables:
            for row in copied[table]:
                keys=list(row)
                conn.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,keys)),sql.SQL(',').join(sql.Placeholder() for _ in keys)),tuple(Jsonb(row[k]) if isinstance(row[k],dict) else row[k] for k in keys))
            if copied[table] and 'id' in copied[table][0]:
                conn.execute("SELECT setval(pg_get_serial_sequence(%s,'id'),GREATEST(1,(SELECT COALESCE(MAX(id),1) FROM "+table+")))",(table,))
        tag='alartqa'+secrets.token_hex(4)
        uid=conn.execute("INSERT INTO users(telegram_id,name,username,language) VALUES (%s,%s,%s,'fa') RETURNING id",(990000000001,tag,tag)).fetchone()['id']
        conn.execute('INSERT INTO wallets(user_id,balance_minor) VALUES (%s,1000000000)',(uid,))
        conn.execute("INSERT INTO admins(user_id,role) VALUES (%s,'owner')",(uid,))
        conn.execute("UPDATE managed_panels SET health='online',last_ok_at=now()")
        # Add a tiny paid QA plan on the trial's real routing, without changing any production catalog.
        original=conn.execute("SELECT p.*,l.id AS location_id FROM plans p JOIN services s ON s.id=p.service_id CROSS JOIN locations l WHERE s.trial_enabled AND s.enabled AND p.enabled AND p.price_minor=0 AND l.enabled ORDER BY p.id,l.id LIMIT 1").fetchone()
        assert original,'No enabled trial plan to test'
        paid_id=conn.execute("INSERT INTO plans(service_id,name,duration_hours,price_minor,currency,traffic_gb,user_limit,stars_price) VALUES (%s,'QA paid acceptance',24,10000,'IRR',1,1,1) RETURNING id",(original['service_id'],)).fetchone()['id']
    user={'id':uid,'telegram_id':990000000001,'name':tag,'language':'fa','role':'owner'}
    app.dependency_overrides[current_user]=lambda:user
    app.dependency_overrides[require_owner]=lambda:user
    domain=os.environ['DOMAIN']
    client=TestClient(app,base_url='https://'+domain,headers={'Origin':'https://'+domain})
    with patch('alart.bot.message') as delivered,patch('alart.bot.qr_photo'):
        response=client.post('/api/trials',json={'service_id':original['service_id'],'location_id':original['location_id']})
        assert response.status_code==200,response.text
        trial_id=response.json()['id']
        assert worker.process_one()
        with transaction() as conn:
            configs=conn.execute('SELECT c.*,p.base_url,p.credential_ciphertext FROM provisioned_configurations c JOIN managed_panels p ON p.id=c.panel_id').fetchall();created.extend(configs)
            sub=conn.execute('SELECT * FROM subscriptions WHERE order_id=%s',(trial_id,)).fetchone()
        assert sub['status']=='active',sub['status']
        assert sub['provider_subscription_url']
        actual=client.get(f"/api/subscriptions/{sub['id']}/subscription-link").json()['url']
        assert actual==sub['provider_subscription_url']
        import httpx
        page=httpx.get(actual,headers={'Accept':'text/html'},timeout=20,follow_redirects=False)
        assert page.status_code==200 and len(page.content)>100,('subscription page',page.status_code)
        assert client.get(f"/api/subscriptions/{sub['id']}/qr").content.startswith(b'\x89PNG')
        assert delivered.called
        passed('trial -> real Sanaei client -> subscription URL -> owner QR')
        r=client.post('/api/trials',json={'service_id':original['service_id'],'location_id':original['location_id']})
        assert r.status_code==409
        passed('second trial rejected without another client')
        order=core.purchase(uid,paid_id,original['location_id'],tag+'-paid',payment_method='wallet',expected_final_minor=10000)
        duplicate=core.purchase(uid,paid_id,original['location_id'],tag+'-paid',payment_method='wallet',expected_final_minor=10000)
        assert duplicate['id']==order['id']
        assert worker.process_one()
        with transaction() as conn:
            config=conn.execute('SELECT c.*,p.base_url,p.credential_ciphertext FROM provisioned_configurations c JOIN managed_panels p ON p.id=c.panel_id WHERE c.order_id=%s',(order['id'],)).fetchone();created.append(config)
            paid_sub=conn.execute('SELECT * FROM subscriptions WHERE order_id=%s',(order['id'],)).fetchone()
            assert conn.execute("SELECT count(*) AS n FROM wallet_transactions WHERE user_id=%s AND kind='purchase'",(uid,)).fetchone()['n']<=2
        assert paid_sub['status']=='active'
        passed('paid purchase and retry keep one order and one client')
        renewal=core.renew(uid,paid_sub['id'],tag+'-renew','wallet',10000,paid_id)
        assert worker.process_renewal()
        with transaction() as conn:
            after=conn.execute('SELECT * FROM subscriptions WHERE id=%s',(paid_sub['id'],)).fetchone()
            assert after['remote_id']==paid_sub['remote_id']
            assert after['expires_at']>paid_sub['expires_at']
            assert after['provider_subscription_url']==paid_sub['provider_subscription_url']
            assert conn.execute('SELECT count(*) AS n FROM subscriptions').fetchone()['n']==2
            conn.execute("UPDATE subscriptions SET expires_at=now()+interval '3 hours',traffic_used_bytes=traffic_limit_bytes-10000000 WHERE id=%s",(after['id'],))
        renewed_remote=SanaeiProvider.from_panel(config).get_client(config['client_email'])
        assert renewed_remote['enable'] is True
        assert renewed_remote.get('subId') and after['provider_subscription_url'].endswith(renewed_remote['subId']), 'renewal changed remote subscription identity'
        lifecycle.reminders();lifecycle.reminders()
        with transaction() as conn:
            notices=conn.execute('SELECT event_key,body,action_path FROM notifications WHERE event_key IS NOT NULL').fetchall()
            assert len(notices)==len({n['event_key'] for n in notices})
            assert any('ساعت' in n['body'] for n in notices)
            assert any('مگابایت' in n['body'] for n in notices)
        passed('renewal preserves subscription identity; bilingual low-time/traffic notices deduplicate')
        # Simulate elapsed time in QA data and disable only our QA client.
        with transaction() as conn:
            conn.execute("UPDATE subscriptions SET expires_at=now()-interval '1 hour' WHERE id=%s",(after['id'],))
        assert client.get('/api/account').json()['subscriptions'][0]['status']=='expired'
        assert client.get(f"/api/subscriptions/{after['id']}/qr").status_code==404
        worker.expire_remote_services();lifecycle.reminders()
        passed('expired account hides stale connection and QR before/after worker')
        # Delete only the QA trial, verifying its exact owner before mutation.
        trial_config=created[0];provider=SanaeiProvider.from_panel(trial_config)
        remote=provider.get_client(trial_config['client_email'])
        assert int(remote['tgId'])==990000000001 and trial_config['client_email'].startswith(tag)
        from urllib.parse import quote
        provider._request('POST','clients/del/'+quote(trial_config['client_email'],safe=''))
        lifecycle.reconcile()
        rows=client.get('/api/account').json()['subscriptions']
        missing=next(x for x in rows if x['id']==sub['id'])
        assert missing['status']=='expired' and missing['remote_missing_at']
        assert client.post(f"/api/subscriptions/{sub['id']}/archive",json={}).status_code==200
        assert all(x['id']!=sub['id'] for x in client.get('/api/account').json()['subscriptions'])
        passed('confirmed panel deletion removes stale link; archive hides ended service')
        guides=client.get('/api/guides').json();assert len(guides)==4
        r=client.put('/api/admin/guides',json={'platform':'ios','language':'fa','title':'آموزش آزمایشی','body':'متن آزمایشی','published':True});assert r.status_code==200,r.text
        assert any(x['title']=='آموزش آزمایشی' for x in client.get('/api/guides').json())
        assert client.put('/api/admin/guides/video',content=b'not video',headers={'Content-Type':'video/mp4'}).status_code==422
        passed('device/language guide editing and invalid-video rejection')
        # A failed receipt upload must remain retryable, including after reopening.
        with transaction() as conn:
            conn.execute("UPDATE payment_settings SET enabled=true,public_details='QA destination' WHERE provider='manual'")
        r=client.post('/api/orders',json={'plan_id':paid_id,'location_id':original['location_id'],'payment_method':'manual','request_key':tag+'-manual','expected_final_minor':10000})
        assert r.status_code==200,r.text
        manual_id=r.json()['id']
        submission={'order_id':manual_id,'reference':'QA receipt'}
        assert client.post('/api/payments/manual/submit',json=submission).status_code==200
        assert client.put(f'/api/payments/manual/{manual_id}/receipt',content=b'invalid',headers={'Content-Type':'image/png'}).status_code==415
        assert client.post('/api/payments/manual/submit',json=submission).status_code==200
        from alart.qr import provider_subscription_png
        png=provider_subscription_png('https://example.com/qa')
        assert client.put(f'/api/payments/manual/{manual_id}/receipt',content=png,headers={'Content-Type':'image/png'}).status_code==200
        assert client.post('/api/payments/manual/submit',json=submission).status_code==409
        with transaction() as conn:
            conn.execute("UPDATE manual_payment_submissions SET review_status='more_info' WHERE payment_id=(SELECT id FROM payments WHERE order_id=%s)",(manual_id,))
        assert client.post('/api/payments/manual/submit',json=submission).status_code==200
        assert client.put(f'/api/payments/manual/{manual_id}/receipt',content=png,headers={'Content-Type':'image/png'}).status_code==200
        passed('manual receipt failure/retry and more-information resubmission')
        timings=[]
        for _ in range(5):
            started=time.perf_counter();r=client.get('/api/bootstrap');timings.append((time.perf_counter()-started)*1000);assert r.status_code==200,r.text
        print('BOOTSTRAP_MS',json.dumps([round(t,1) for t in timings]),flush=True)
    print('ACCEPTANCE_OK',len(results),flush=True)
finally:
    from urllib.parse import quote
    # Find clients even when provisioning failed after the remote write.
    try:
        with transaction() as conn:
            all_configs=conn.execute('SELECT c.*,p.base_url,p.credential_ciphertext FROM provisioned_configurations c JOIN managed_panels p ON p.id=c.panel_id').fetchall()
        for item in all_configs:
            if not item['client_email'].startswith(tag):continue
            provider=SanaeiProvider.from_panel(item)
            remote=provider.get_client(item['client_email'])
            if remote and int(remote.get('tgId') or 0)==990000000001:
                provider._request('POST','clients/del/'+quote(item['client_email'],safe=''))
        print('QA_REMOTE_CLEANUP_OK',flush=True)
    except Exception as error:
        print('QA_REMOTE_CLEANUP_FAILED',type(error).__name__,flush=True)
    if pool():pool().close()
    with psycopg.connect(production,autocommit=True) as source:
        source.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(db_name)))
    print('QA_DATABASE_REMOVED',flush=True)
