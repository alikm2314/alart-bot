import json
import hashlib
import time
import logging
from collections import defaultdict, deque
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .auth import verify_telegram, issue_session, current_user, require_admin, require_operator, require_owner, csrf
from .config import settings
from .db import transaction, migrate
from .core import purchase, trial, ledger, renew
from .ai import answer
from .payment_routes import router as payment_router
from .panel_routes import router as panel_router
from .finance_routes import router as finance_router
from .refunds import router as refund_router

app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
app.include_router(payment_router)
app.include_router(panel_router)
app.include_router(finance_router)
app.include_router(refund_router)
from .guides import router as guides_router
app.include_router(guides_router)
_attempts=defaultdict(deque)
logger=logging.getLogger("alart.api")

@app.on_event("startup")
def start():
    settings()
    migrate()

@app.middleware("http")
async def headers(request: Request, call_next):
    if request.url.path in ("/api/auth/panel","/api/auth/customer-code","/api/auth/telegram","/api/assistant","/api/support/requests") or request.url.path.startswith("/api/payments/manual/"):
        address=request.client.host if request.client else "unknown"
        key=(address,"/api/payments/manual" if request.url.path.startswith("/api/payments/manual/") else request.url.path)
        now=time.monotonic()
        bucket=_attempts[key]
        while bucket and bucket[0]<now-60:
            bucket.popleft()
        limit=10 if request.url.path in ("/api/assistant","/api/support/requests") else 20 if request.url.path.startswith("/api/payments/manual/") else 30
        if len(bucket)>=limit:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail":"Too many requests"},status_code=429)
        bucket.append(now)
        if len(_attempts)>10000:
            for stale in [k for k,v in _attempts.items() if not v or v[-1]<now-60]:
                _attempts.pop(stale,None)
    response=await call_next(request)
    if "Cache-Control" not in response.headers:
        response.headers["Cache-Control"]="no-store" if request.url.path.startswith(("/api/","/subscription/")) else "no-cache" if request.url.path.endswith('/') else "public, max-age=300"
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'self' https://web.telegram.org"
    return response

class Login(BaseModel):
    init_data:str=Field(min_length=10,max_length=10000)
    include_bootstrap:bool=False
class Buy(BaseModel):
    plan_id:int
    location_id:int
    request_key:str
    coupon_code:str|None=None
    expected_final_minor:int=Field(ge=0)
    payment_method:str="wallet"
    users:int|None=None
    traffic_gb:int|None=None
    duration_days:int|None=None
class Quote(BaseModel):
    plan_id:int
    coupon_code:str|None=None
    payment_method:str="wallet"
    users:int|None=None
    traffic_gb:int|None=None
    duration_days:int|None=None
class Renew(BaseModel):
    request_key:str
    payment_method:str="stars"
    expected_final_minor:int=Field(ge=0)
    plan_id:int|None=None
class Trial(BaseModel):
    service_id:int
    location_id:int
class Ask(BaseModel):
    question:str=Field(min_length=1,max_length=1000)
class SupportRequest(BaseModel):
    body:str=Field(min_length=1,max_length=1500)
class AdminMessage(BaseModel):
    body:str=Field(min_length=1,max_length=1500)
    buttons:list['MessageButton']=Field(default_factory=list,max_length=3)
class MessageButton(BaseModel):
    label:str=Field(min_length=1,max_length=64)
    surface:str=Field(default='miniapp',pattern=r"^(chat|miniapp)$")
    action:str=Field(pattern=r"^(buy|services|miniapp|wallet|guide|account|support)$")
    style:str=Field(default='primary',pattern=r"^(primary|success|danger)$")
    coupon_code:str|None=Field(default=None,pattern=r"^[A-Za-z0-9_-]{1,40}$")
class BroadcastMessage(AdminMessage):
    audience:str=Field(pattern=r"^(all|purchased|no_purchase|trial_only|active_service|manual_payers|stars_payers|nowpayments_payers|wallet_payers)$")
class Deposit(BaseModel):
    user_id:int
    amount_minor:int=Field(gt=0)
    reference:str=Field(min_length=8,max_length=120)
class Ack(BaseModel):
    alert_id:int
class PanelLogin(BaseModel):
    code:str=Field(min_length=20,max_length=100)
class CustomerLogin(BaseModel):
    code:str=Field(min_length=20,max_length=100)
class LanguageChoice(BaseModel):
    language:str=Field(pattern=r"^(fa|en)$")
class ReferralSettingsUpdate(BaseModel):
    enabled:bool
    referrer_reward_minor:int=Field(ge=0,le=100000000000)
    referred_reward_minor:int=Field(ge=0,le=100000000000)
class LocationCreate(BaseModel):
    name:str=Field(min_length=2,max_length=100)
class ServiceCreate(BaseModel):
    name:str=Field(min_length=2,max_length=100)
    description:str=Field(default="",max_length=500)
    trial_enabled:bool=False
class PlanCreate(BaseModel):
    service_id:int
    name:str=Field(min_length=2,max_length=100)
    duration_hours:int=Field(ge=0,le=8760)
    price_minor:int=Field(ge=0)
class ServerCreate(BaseModel):
    location_id:int
    name:str=Field(min_length=2,max_length=100)
    api_url:str=Field(pattern=r"^https://",max_length=500)
    api_token:str=Field(min_length=16,max_length=1000)
    capacity:int=Field(gt=0)

@app.get("/api/health")
def health():
    with transaction() as conn:
        conn.execute("SELECT 1")
    return {"status":"ok","version":Path("VERSION").read_text().strip()}

@app.post("/api/auth/telegram")
def login(body:Login,response:Response):
    identity=verify_telegram(body.init_data)
    telegram_id=int(identity["id"])
    name=str(identity.get("first_name",""))[:100]
    username=str(identity.get("username") or "")[:100]
    photo_url=str(identity.get("photo_url") or "")[:1000]
    if not photo_url.startswith("https://"):
        photo_url=""
    with transaction() as conn:
        user=conn.execute("INSERT INTO users(telegram_id,name,username,photo_url) VALUES (%s,%s,%s,%s) ON CONFLICT (telegram_id) DO UPDATE SET name=EXCLUDED.name,username=EXCLUDED.username,photo_url=EXCLUDED.photo_url RETURNING id",(telegram_id,name,username,photo_url)).fetchone()
        from .ops_topics import emit
        emit(conn,"users.new",user["id"])
        conn.execute("INSERT INTO wallets(user_id) VALUES (%s) ON CONFLICT DO NOTHING",(user["id"],))
        if telegram_id==int(settings()["ADMIN_TELEGRAM_ID"]):
            conn.execute("INSERT INTO admins(user_id,role) VALUES (%s,'owner') ON CONFLICT DO NOTHING",(user["id"],))
    response.set_cookie("alart_session",issue_session(user["id"],telegram_id==int(settings()["ADMIN_TELEGRAM_ID"])),secure=True,httponly=True,samesite="strict",max_age=86400,path="/")
    if body.include_bootstrap:
        with transaction() as conn:
            profile=conn.execute('SELECT u.id,u.telegram_id,u.name,u.language,a.role FROM users u LEFT JOIN admins a ON a.user_id=u.id WHERE u.id=%s',(user['id'],)).fetchone()
        return {'ok':True,'bootstrap':bootstrap(profile)}
    return {"ok":True}

@app.post("/api/auth/logout")
def logout(request:Request,response:Response):
    csrf(request)
    response.delete_cookie("alart_session",path="/")
    return {"ok":True}

@app.post("/api/auth/panel")
def panel_login(body:PanelLogin,response:Response,request:Request):
    csrf(request)
    digest=hashlib.sha256(body.code.encode()).hexdigest()
    with transaction() as conn:
        row=conn.execute("UPDATE admin_login_codes SET used_at=now() WHERE code_hash=%s AND used_at IS NULL AND expires_at>now() RETURNING user_id",(digest,)).fetchone()
        if not row or not conn.execute("SELECT 1 FROM admins WHERE user_id=%s",(row["user_id"],)).fetchone():
            raise HTTPException(401,"Invalid or expired code")
        from .ops_topics import emit
        emit(conn,"security.admin_login",row["user_id"],key=str(time.time_ns()))
    response.set_cookie("alart_session",issue_session(row["user_id"],True),secure=True,httponly=True,samesite="strict",max_age=86400,path="/")
    return {"ok":True}

@app.post("/api/auth/customer-code")
def customer_code(body:CustomerLogin,response:Response,request:Request):
    csrf(request)
    digest=hashlib.sha256(body.code.encode()).hexdigest()
    with transaction() as conn:
        row=conn.execute("UPDATE customer_login_codes SET used_at=now() WHERE code_hash=%s AND used_at IS NULL AND expires_at>now() RETURNING user_id",(digest,)).fetchone()
        if not row:
            raise HTTPException(401,"Invalid or expired code")
    response.set_cookie("alart_session",issue_session(row["user_id"]),secure=True,httponly=True,samesite="strict",max_age=86400,path="/")
    return {"ok":True}

@app.get("/api/me")
def me(user=Depends(current_user)):
    with transaction() as conn:
        wallet=conn.execute("SELECT balance_minor FROM wallets WHERE user_id=%s",(user["id"],)).fetchone()
        profile=conn.execute("SELECT username,photo_url,created_at FROM users WHERE id=%s",(user['id'],)).fetchone()
    return {"name":user["name"],"telegram_id":user["telegram_id"],"role":user["role"],"language":user["language"],"balance_minor":wallet["balance_minor"] if wallet else 0,
        "username":profile['username'],"photo_url":profile['photo_url'],"created_at":profile['created_at']}


@app.get('/api/me/avatar')
def my_avatar(user=Depends(current_user)):
    """Serve only the caller's Telegram profile image from our origin for CSP-safe display."""
    import httpx
    token=settings()['BOT_TOKEN']
    try:
        with httpx.Client(timeout=4.0) as client:
            photos=client.get(f'https://api.telegram.org/bot{token}/getUserProfilePhotos',params={'user_id':user['telegram_id'],'limit':1}).json()
            sizes=((photos.get('result') or {}).get('photos') or [[]])[0]
            if not photos.get('ok') or not sizes: return Response(status_code=204)
            file_id=(sizes[-1] or {}).get('file_id')
            meta=client.get(f'https://api.telegram.org/bot{token}/getFile',params={'file_id':file_id}).json()
            file_path=(meta.get('result') or {}).get('file_path') or ''
            if not meta.get('ok') or not file_path.startswith('photos/'): return Response(status_code=204)
            image=client.get(f'https://api.telegram.org/file/bot{token}/{file_path}').content
        if not image or len(image)>2*1024*1024: return Response(status_code=204)
        return Response(content=image,media_type='image/jpeg',headers={'Cache-Control':'private, max-age=3600'})
    except Exception:
        return Response(status_code=204)

def _audience_sql(audience:str):
    clauses={
      'all':'TRUE',
      'purchased':'EXISTS (SELECT 1 FROM orders o WHERE o.user_id=u.id AND o.status IN (\'paid\',\'provisioning\',\'active\'))',
      'no_purchase':'NOT EXISTS (SELECT 1 FROM orders o WHERE o.user_id=u.id AND o.status IN (\'paid\',\'provisioning\',\'active\'))',
      'trial_only':'EXISTS (SELECT 1 FROM trial_claims t WHERE t.user_id=u.id) AND NOT EXISTS (SELECT 1 FROM orders o JOIN plans p ON p.id=o.plan_id WHERE o.user_id=u.id AND p.price_minor>0 AND o.status IN (\'paid\',\'provisioning\',\'active\'))',
      'active_service':'EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id=u.id AND s.status IN (\'active\',\'expiring\'))',
      'manual_payers':"EXISTS (SELECT 1 FROM orders o WHERE o.user_id=u.id AND o.payment_method='manual' AND o.status IN ('paid','provisioning','active'))",
      'stars_payers':"EXISTS (SELECT 1 FROM orders o WHERE o.user_id=u.id AND o.payment_method='stars' AND o.status IN ('paid','provisioning','active'))",
      'nowpayments_payers':"EXISTS (SELECT 1 FROM orders o WHERE o.user_id=u.id AND o.payment_method='nowpayments' AND o.status IN ('paid','provisioning','active'))",
      'wallet_payers':"EXISTS (SELECT 1 FROM orders o WHERE o.user_id=u.id AND o.payment_method='wallet' AND o.status IN ('paid','provisioning','active'))",
    }
    return clauses[audience]

@app.get('/api/admin/users')
def admin_users(query:str='',owner=Depends(require_owner)):
    query=query.strip()[:100]
    with transaction() as conn:
        return conn.execute("""SELECT u.telegram_id,u.name,u.username,u.created_at,
          coalesce(w.balance_minor,0) AS balance_minor,count(DISTINCT o.id) AS orders,
          count(DISTINCT s.id) FILTER (WHERE s.status IN ('active','expiring')) AS active_services
          FROM users u LEFT JOIN wallets w ON w.user_id=u.id LEFT JOIN orders o ON o.user_id=u.id
          LEFT JOIN subscriptions s ON s.user_id=u.id
          WHERE (%s='' OR u.telegram_id::text=%s OR u.name ILIKE '%%'||%s||'%%' OR u.username ILIKE '%%'||%s||'%%')
          GROUP BY u.id,w.balance_minor ORDER BY u.created_at DESC LIMIT 150""",(query,query,query,query)).fetchall()

@app.get('/api/admin/users/{telegram_id}')
def admin_user_detail(telegram_id:int,owner=Depends(require_owner)):
    with transaction() as conn:
        user=conn.execute("SELECT u.id,u.telegram_id,u.name,u.username,u.photo_url,u.created_at,coalesce(w.balance_minor,0) balance_minor FROM users u LEFT JOIN wallets w ON w.user_id=u.id WHERE u.telegram_id=%s",(telegram_id,)).fetchone()
        if not user: raise HTTPException(404,'User not found')
        orders=conn.execute("SELECT o.id,o.status,o.payment_method,o.price_minor,o.currency,o.created_at,p.name AS plan,(SELECT m.review_status FROM manual_payment_submissions m JOIN payments pay ON pay.id=m.payment_id WHERE pay.order_id=o.id AND pay.provider='manual') AS review_status,(SELECT m.receipt_path IS NOT NULL FROM manual_payment_submissions m JOIN payments pay ON pay.id=m.payment_id WHERE pay.order_id=o.id AND pay.provider='manual') AS has_receipt FROM orders o JOIN plans p ON p.id=o.plan_id WHERE o.user_id=%s ORDER BY o.id DESC LIMIT 20",(user['id'],)).fetchall()
        services=conn.execute("SELECT id,status,expires_at,traffic_limit_bytes,traffic_used_bytes FROM subscriptions WHERE user_id=%s ORDER BY id DESC LIMIT 20",(user['id'],)).fetchall()
        topups=conn.execute("SELECT id,provider,status,amount_minor,customer_reference,created_at FROM wallet_topups WHERE user_id=%s ORDER BY id DESC LIMIT 20",(user['id'],)).fetchall()
    return {'user':user,'orders':orders,'services':services,'topups':topups}

@app.post('/api/admin/users/{telegram_id}/message')
def admin_user_message(telegram_id:int,body:AdminMessage,request:Request,owner=Depends(require_owner)):
    csrf(request)
    with transaction() as conn:
        user=conn.execute('SELECT id,telegram_id FROM users WHERE telegram_id=%s',(telegram_id,)).fetchone()
        if not user: raise HTTPException(404,'User not found')
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id,detail) VALUES (%s,'customer.message','user',%s,%s)",(owner['id'],user['id'],json.dumps({'length':len(body.body),'buttons':len(body.buttons)})))
    try:
        from .bot import message
        from .message_actions import markup
        message(user['telegram_id'],body.body.strip(),markup([button.model_dump() for button in body.buttons]))
    except Exception:
        raise HTTPException(503,'Telegram could not deliver this message')
    return {'ok':True}

@app.post('/api/admin/messages/broadcast')
def admin_broadcast(body:BroadcastMessage,request:Request,owner=Depends(require_owner)):
    csrf(request)
    with transaction() as conn:
        buttons=[button.model_dump() for button in body.buttons]
        job=conn.execute('INSERT INTO broadcast_jobs(audience,body,buttons,created_by) VALUES (%s,%s,%s,%s) RETURNING id',(body.audience,body.body.strip(),json.dumps(buttons),owner['id'])).fetchone()
        clause=_audience_sql(body.audience)
        conn.execute(f"INSERT INTO broadcast_recipients(job_id,user_id) SELECT %s,u.id FROM users u WHERE {clause} ON CONFLICT DO NOTHING",(job['id'],))
        count=conn.execute('SELECT count(*) AS count FROM broadcast_recipients WHERE job_id=%s',(job['id'],)).fetchone()['count']
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id,detail) VALUES (%s,'customer.broadcast.queue','broadcast_job',%s,%s)",(owner['id'],job['id'],json.dumps({'audience':body.audience,'recipients':count,'length':len(body.body),'buttons':len(buttons)})))
    return {'id':job['id'],'recipients':count}

@app.post("/api/me/language")
def set_language(body:LanguageChoice,request:Request,user=Depends(current_user)):
    csrf(request)
    with transaction() as conn:
        conn.execute("UPDATE users SET language=%s WHERE id=%s",(body.language,user["id"]))
    return {"language":body.language}

@app.get("/api/referrals/me")
def my_referrals(user=Depends(current_user)):
    from .referral import ensure_code, referral_link
    with transaction() as conn:
        code=ensure_code(conn,user["id"])
        counts=conn.execute("""SELECT count(*) AS invited,
          count(*) FILTER (WHERE status='awarded') AS successful
          FROM referrals WHERE referrer_user_id=%s""",(user["id"],)).fetchone()
        earnings=conn.execute("""SELECT coalesce(sum(CASE WHEN rr.status='awarded' THEN rr.amount_minor ELSE 0 END),0) AS total_minor
          FROM referral_rewards rr WHERE rr.beneficiary_user_id=%s AND rr.role='referrer'""",(user["id"],)).fetchone()
        wallet=conn.execute("SELECT balance_minor FROM wallets WHERE user_id=%s",(user["id"],)).fetchone()
    return {"code":code,"link":referral_link(code),"invited":counts["invited"],"successful":counts["successful"],"total_earnings_minor":earnings["total_minor"],"wallet_balance_minor":wallet["balance_minor"] if wallet else 0}

@app.get("/api/admin/referrals/settings")
def referral_settings(admin=Depends(require_admin)):
    with transaction() as conn:
        return conn.execute("SELECT enabled,referrer_reward_minor,referred_reward_minor,updated_at FROM referral_settings WHERE singleton=true").fetchone()

@app.put("/api/admin/referrals/settings")
def update_referral_settings(body:ReferralSettingsUpdate,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        row=conn.execute("""UPDATE referral_settings SET enabled=%s,referrer_reward_minor=%s,
          referred_reward_minor=%s,updated_at=now() WHERE singleton=true
          RETURNING enabled,referrer_reward_minor,referred_reward_minor,updated_at""",
          (body.enabled,body.referrer_reward_minor,body.referred_reward_minor)).fetchone()
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,'referrals.settings.update','referral_settings',1)",(admin["id"],))
    return row

@app.get("/api/admin/referrals")
def admin_referrals(admin=Depends(require_admin)):
    with transaction() as conn:
        return conn.execute("""SELECT r.referred_at,r.qualified_at,r.status,r.qualified_order_id,
          a.telegram_id AS referrer_telegram_id,b.telegram_id AS referred_telegram_id,
          coalesce(sum(rr.amount_minor) FILTER (WHERE rr.status='awarded'),0) AS credited_minor,
          coalesce(sum(rr.amount_minor) FILTER (WHERE rr.status='reversal_pending'),0) AS pending_reversal_minor
          FROM referrals r JOIN users a ON a.id=r.referrer_user_id
          JOIN users b ON b.id=r.referred_user_id
          LEFT JOIN referral_rewards rr ON rr.referred_user_id=r.referred_user_id
          GROUP BY r.referred_user_id,r.referred_at,r.qualified_at,r.status,r.qualified_order_id,a.telegram_id,b.telegram_id
          ORDER BY r.referred_at DESC LIMIT 100""").fetchall()


@app.post('/api/client-failures')
def client_failure(body:dict,user=Depends(current_user)):
    """Diagnostic event only: bounded browser error data, never credentials or request payloads."""
    stage=str(body.get('stage') or 'unknown')[:48]
    error=str(body.get('error') or 'unknown')[:160]
    error=''.join(char for char in error if char.isprintable())
    logger.warning('miniapp_boot_failure user_id=%s stage=%s error=%s',user['id'],stage,error)
    return {'ok':True}

@app.get("/api/bootstrap")
def bootstrap(user=Depends(current_user)):
    # One startup round trip keeps the Mini App responsive on a small VPS.
    data={"catalog":catalog(),"me":me(user),"account":account(user)}
    with transaction() as conn:
        data["notifications"]=conn.execute("SELECT id,body,action_path,created_at FROM notifications WHERE user_id=%s ORDER BY id DESC LIMIT 20",(user['id'],)).fetchall()
        data["trial_eligible"]=not bool(conn.execute("SELECT 1 FROM trial_claims WHERE user_id=%s LIMIT 1",(user['id'],)).fetchone())
        contact=conn.execute("SELECT u.name,u.username,u.telegram_id FROM support_settings s LEFT JOIN users u ON u.id=s.support_user_id WHERE s.singleton=true").fetchone()
        data['support_contact']=contact if contact and contact['telegram_id'] else None
    return data

@app.post('/api/support/requests')
def create_support_request(body:SupportRequest,request:Request,user=Depends(current_user)):
    csrf(request)
    message_body=body.body.strip()
    with transaction() as conn:
        contact=conn.execute("SELECT u.telegram_id,u.name FROM support_settings s JOIN users u ON u.id=s.support_user_id WHERE s.singleton=true").fetchone()
        if not contact:
            raise HTTPException(409,'Support is not configured yet')
        row=conn.execute("INSERT INTO support_requests(user_id,body) VALUES (%s,%s) RETURNING id",(user['id'],message_body)).fetchone()
        sender_row=conn.execute("SELECT username,name FROM users WHERE id=%s",(user['id'],)).fetchone()
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,'support.request','support_request',%s)",(user['id'],row['id']))
    try:
        from .bot import message as telegram_message
        sender=f"@{sender_row['username']}" if sender_row['username'] else sender_row['name'] or 'Customer'
        telegram_message(contact['telegram_id'],f"💬 Support request #{row['id']}\nFrom: {sender}\nTelegram ID: {user['telegram_id']}\n\n{message_body}")
    except Exception:
        raise HTTPException(503,'Support could not receive the message; please try again')
    return {'ok':True,'id':row['id']}

@app.get('/api/admin/support')
def admin_support(owner=Depends(require_owner)):
    with transaction() as conn:
        return conn.execute("SELECT u.telegram_id,u.name,u.username FROM support_settings s LEFT JOIN users u ON u.id=s.support_user_id WHERE s.singleton=true").fetchone()

@app.put('/api/admin/support')
def admin_support_update(body:dict,request:Request,owner=Depends(require_owner)):
    csrf(request)
    telegram_id=body.get('telegram_id')
    if type(telegram_id) is not int or telegram_id<=0: raise HTTPException(422,'Enter a valid numeric Telegram ID')
    with transaction() as conn:
        target=conn.execute('SELECT id FROM users WHERE telegram_id=%s FOR UPDATE',(telegram_id,)).fetchone()
        if not target: raise HTTPException(404,'This support user has not started the bot yet')
        conn.execute('UPDATE support_settings SET support_user_id=%s,updated_at=now() WHERE singleton=true',(target['id'],))
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,'support.settings.update','support_settings',1)",(owner['id'],))
    return {'ok':True}

@app.get("/api/catalog")
def catalog():
    with transaction() as conn:
        services=conn.execute("SELECT id,name,description,trial_enabled,category FROM services WHERE enabled AND NOT internal ORDER BY id").fetchall()
        plans=conn.execute("SELECT p.id,p.service_id,p.name,p.duration_hours,p.price_minor,p.currency,p.traffic_gb,p.user_limit,p.stars_price,p.is_custom FROM plans p JOIN services s ON s.id=p.service_id WHERE p.enabled AND s.enabled AND NOT s.internal ORDER BY p.price_minor").fetchall()
        locations=conn.execute("SELECT id,name FROM locations WHERE enabled ORDER BY name").fetchall()
        announcements=conn.execute("SELECT title,body FROM announcements WHERE published ORDER BY id DESC LIMIT 3").fetchall()
        stars_enabled=bool(conn.execute("SELECT 1 FROM payment_settings WHERE provider='stars' AND enabled").fetchone())
        web_payment_methods=[row["provider"] for row in conn.execute("SELECT provider FROM payment_settings WHERE provider IN ('nowpayments','manual') AND enabled ORDER BY provider").fetchall()]
        custom_volume_offers=conn.execute("""SELECT o.category,p.id AS plan_id,s.id AS service_id,p.name,p.duration_hours,p.traffic_gb,p.user_limit,p.price_minor,p.currency,p.stars_price,p.is_custom
          FROM custom_volume_offers o JOIN services s ON s.id=o.service_id JOIN plans p ON p.service_id=s.id AND p.is_custom AND p.enabled
          WHERE o.enabled AND s.enabled ORDER BY o.category""").fetchall()
    return {"services":services,"plans":plans,"locations":locations,"announcements":announcements,"stars_enabled":stars_enabled,"web_payment_methods":["wallet",*web_payment_methods],"custom_volume_offers":custom_volume_offers}

@app.get("/api/services/{service_id}/custom-limits")
def custom_limits(service_id:int):
    with transaction() as conn:
        row=conn.execute("SELECT min_users,max_users,min_traffic_gb,max_traffic_gb,min_days,max_days FROM pricing_rules WHERE service_id=%s AND enabled",(service_id,)).fetchone()
    if not row:
        raise HTTPException(404,"Custom builder unavailable")
    return row

@app.get('/api/notifications')
def customer_notifications(user=Depends(current_user)):
    with transaction() as conn:
        return conn.execute('SELECT id,body,action_path,created_at FROM notifications WHERE user_id=%s ORDER BY id DESC LIMIT 20',(user['id'],)).fetchall()

@app.get("/api/account")
def account(user=Depends(current_user)):
    with transaction() as conn:
        orders=conn.execute("SELECT o.id,o.plan_id,o.renewal_of_sub_id,o.status,o.price_minor,o.currency,o.payment_method,o.selection,o.created_at,p.name AS plan,(SELECT m.review_status FROM manual_payment_submissions m JOIN payments pay ON pay.id=m.payment_id WHERE pay.order_id=o.id AND pay.provider='manual') AS review_status,(SELECT m.receipt_path IS NOT NULL FROM manual_payment_submissions m JOIN payments pay ON pay.id=m.payment_id WHERE pay.order_id=o.id AND pay.provider='manual') AS has_receipt,r.status AS refund_status,r.confirmed_at AS refund_confirmed_at FROM orders o JOIN plans p ON p.id=o.plan_id LEFT JOIN refund_requests r ON r.order_id=o.id WHERE o.user_id=%s ORDER BY o.id DESC LIMIT 30",(user["id"],)).fetchall()
        subs=conn.execute("""SELECT s.id,s.order_id,s.status,s.configuration,s.provider_subscription_url,s.expires_at,s.traffic_limit_bytes,s.traffic_used_bytes,s.traffic_sampled_at,s.user_limit,s.remote_missing_at,
            p.name AS plan_name,p.duration_hours,p.price_minor=0 AS is_trial,svc.category,l.name AS location_name FROM subscriptions s
            JOIN orders o ON o.id=s.order_id JOIN plans p ON p.id=o.plan_id
            JOIN services svc ON svc.id=p.service_id
            JOIN servers v ON v.id=s.server_id JOIN locations l ON l.id=v.location_id
            WHERE s.user_id=%s AND s.archived_at IS NULL ORDER BY s.id DESC LIMIT 30""",(user["id"],)).fetchall()
        transactions=conn.execute("SELECT amount_minor,kind,balance_after_minor,created_at FROM wallet_transactions WHERE user_id=%s ORDER BY id DESC LIMIT 30",(user["id"],)).fetchall()
    from .service_state import present
    return {"orders":orders,"subscriptions":[present(sub) for sub in subs],"transactions":transactions}

def _subscription_url(token:str)->str:
    return f"https://{settings()['DOMAIN']}/subscription/{token}"

def _subscription_token(conn,subscription_id:int)->str:
    import secrets
    row=conn.execute("SELECT subscription_token FROM subscriptions WHERE id=%s FOR UPDATE",(subscription_id,)).fetchone()
    if not row: raise HTTPException(404,"Subscription not found")
    if row['subscription_token']: return row['subscription_token']
    for _ in range(3):
        token=secrets.token_urlsafe(32)
        try:
            updated=conn.execute("UPDATE subscriptions SET subscription_token=%s WHERE id=%s AND subscription_token IS NULL RETURNING subscription_token",(token,subscription_id)).fetchone()
            if updated:return updated['subscription_token']
        except Exception: continue
    raise HTTPException(503,"Could not create subscription link")

@app.get("/api/subscriptions/{subscription_id}/subscription-link")
def subscription_link(subscription_id:int,user=Depends(current_user)):
    with transaction() as conn:
        row=conn.execute("SELECT provider_subscription_url FROM subscriptions WHERE id=%s AND user_id=%s AND status IN ('active','expiring') AND (expires_at IS NULL OR expires_at>now()) AND (traffic_limit_bytes IS NULL OR traffic_limit_bytes=0 OR COALESCE(traffic_used_bytes,0)<traffic_limit_bytes)",(subscription_id,user['id'])).fetchone()
        if not row: raise HTTPException(404,"Active subscription not found")
    if not row['provider_subscription_url']: raise HTTPException(409,"Subscription URL is not available from the panel yet")
    return {'url':row['provider_subscription_url']}

@app.get("/subscription/{token}")
def public_subscription(token:str):
    if len(token)<32 or len(token)>128: raise HTTPException(404,"Subscription not found")
    with transaction() as conn:
        row=conn.execute("SELECT configuration FROM subscriptions WHERE subscription_token=%s AND status IN ('active','expiring')",(token,)).fetchone()
    if not row or not row['configuration']: raise HTTPException(404,"Subscription not found")
    return Response(row['configuration'].strip()+"\n",media_type='text/plain; charset=utf-8',headers={'Cache-Control':'no-store','Content-Disposition':'inline'})

@app.get("/api/subscriptions/{subscription_id}/qr")
def subscription_qr(subscription_id:int,user=Depends(current_user)):
    from .qr import provider_subscription_png
    url=subscription_link(subscription_id,user)['url']
    return Response(provider_subscription_png(url),media_type="image/png",headers={"Cache-Control":"no-store","Content-Disposition":"inline"})

@app.get("/api/subscriptions/{subscription_id}/usage")
def subscription_usage(subscription_id:int,user=Depends(current_user)):
    from .sanaei import SanaeiProvider, PanelError
    with transaction() as conn:
        row=conn.execute("""SELECT s.id,s.status,s.traffic_limit_bytes,s.traffic_used_bytes,s.traffic_sampled_at,s.traffic_check_at,
          c.client_email,p.base_url,p.credential_ciphertext
          FROM subscriptions s LEFT JOIN provisioned_configurations c ON c.subscription_id=s.id
          LEFT JOIN managed_panels p ON p.id=c.panel_id
          WHERE s.id=%s AND s.user_id=%s""",(subscription_id,user["id"])).fetchone()
        if not row:
            raise HTTPException(404,"Subscription not found")
        if row["client_email"] and row["status"] in ("active","expiring"):
            due=conn.execute("""UPDATE subscriptions SET traffic_check_at=now() WHERE id=%s
              AND (traffic_sampled_at IS NULL OR traffic_sampled_at<now()-interval '5 minutes')
              AND (traffic_check_at IS NULL OR traffic_check_at<now()-interval '1 minute')
              RETURNING id""",(subscription_id,)).fetchone()
        else:
            due=None
    if due:
        try:
            traffic=SanaeiProvider.from_panel(row).get_traffic(row["client_email"])
            with transaction() as conn:
                sampled=conn.execute("""UPDATE subscriptions SET traffic_used_bytes=%s,traffic_sampled_at=now()
                  WHERE id=%s AND user_id=%s RETURNING traffic_used_bytes,traffic_sampled_at""",
                  (traffic["used"],subscription_id,user["id"])).fetchone()
            row={**row,**sampled}
        except (PanelError,RuntimeError):
            pass
    used=row["traffic_used_bytes"]
    limit=row["traffic_limit_bytes"]
    return {"available":used is not None,"used_bytes":used,"limit_bytes":limit,
            "remaining_bytes":max(0,limit-used) if limit is not None and limit>0 and used is not None else None,
            "sampled_at":row["traffic_sampled_at"],
            "stale":row["traffic_sampled_at"] is None or (time.time()-row["traffic_sampled_at"].timestamp())>600}

@app.post("/api/subscriptions/{subscription_id}/connection-links/refresh")
def refresh_connection_links(subscription_id:int,request:Request,user=Depends(current_user)):
    """Fetch the current provider links for the owner without exposing panel data."""
    csrf(request)
    from .sanaei import SanaeiProvider, PanelError
    with transaction() as conn:
        row=conn.execute("""SELECT s.id,c.client_email,p.base_url,p.credential_ciphertext
          FROM subscriptions s JOIN provisioned_configurations c ON c.subscription_id=s.id
          JOIN managed_panels p ON p.id=c.panel_id
          WHERE s.id=%s AND s.user_id=%s AND s.status IN ('active','expiring')""",(subscription_id,user["id"])).fetchone()
    if not row:
        raise HTTPException(404,"Active configuration not found")
    try:
        provider=SanaeiProvider.from_panel(row)
        client=provider.get_client(row["client_email"])
        if not client: raise PanelError("Client is unavailable")
        result=provider._client_result(row["client_email"],client)
    except (PanelError,RuntimeError):
        raise HTTPException(502,"Connection links are unavailable from the panel")
    with transaction() as conn:
        updated=conn.execute("UPDATE subscriptions SET configuration=%s,provider_subscription_url=%s WHERE id=%s AND user_id=%s RETURNING configuration,provider_subscription_url",(result["configuration"],result.get("subscription_url"),subscription_id,user["id"])).fetchone()
    return {"configuration":updated["configuration"],"provider_subscription_url":updated["provider_subscription_url"]}

@app.post("/api/quote")
def quote(body:Quote,request:Request,user=Depends(current_user)):
    csrf(request)
    from .pricing import quote as calculate
    with transaction() as conn:
        priced=calculate(conn,plan_id=body.plan_id,payment_method=body.payment_method,users=body.users,traffic_gb=body.traffic_gb,duration_days=body.duration_days,coupon_code=body.coupon_code)
    return {"listed_minor":priced.listed_minor,"discount_minor":priced.discount_minor,"final_minor":priced.final_minor,"currency":priced.currency,"selection":priced.selection,"pricing_reference":priced.pricing_reference,"toman_listed_minor":priced.toman_listed_minor,"toman_discount_minor":priced.toman_discount_minor,"toman_final_minor":priced.toman_final_minor}

@app.post("/api/orders")
def buy(body:Buy,request:Request,user=Depends(current_user)):
    csrf(request)
    if body.payment_method not in ("stars","manual"):
        raise HTTPException(422,"Choose a supported checkout method")
    with transaction() as conn:
        if not conn.execute("SELECT 1 FROM payment_settings WHERE provider=%s AND enabled",(body.payment_method,)).fetchone():
            raise HTTPException(409,"Payment method is unavailable")
    return purchase(user["id"],body.plan_id,body.location_id,body.request_key,body.coupon_code,expected_final_minor=body.expected_final_minor,payment_method=body.payment_method,users=body.users,traffic_gb=body.traffic_gb,duration_days=body.duration_days)

@app.post("/api/external/orders")
def external_buy(body:Buy,request:Request,user=Depends(current_user)):
    csrf(request)
    if body.payment_method not in ("wallet","nowpayments","manual"):
        raise HTTPException(422,"Choose a supported web checkout method")
    with transaction() as conn:
        if body.payment_method!="wallet" and not conn.execute("SELECT 1 FROM payment_settings WHERE provider=%s AND enabled",(body.payment_method,)).fetchone():
            raise HTTPException(409,"Payment method unavailable")
    return purchase(user["id"],body.plan_id,body.location_id,body.request_key,body.coupon_code,expected_final_minor=body.expected_final_minor,payment_method=body.payment_method,users=body.users,traffic_gb=body.traffic_gb,duration_days=body.duration_days)

@app.post("/api/trials")
def create_trial(body:Trial,request:Request,user=Depends(current_user)):
    csrf(request)
    return trial(user["id"],body.service_id,body.location_id)

@app.post("/api/subscriptions/{subscription_id}/renew")
def renew_subscription(subscription_id:int,body:Renew,request:Request,user=Depends(current_user)):
    csrf(request)
    if body.payment_method not in ("wallet","stars"):
        raise HTTPException(403,"This payment method is unavailable in Telegram")
    return renew(user["id"],subscription_id,body.request_key,body.payment_method,body.expected_final_minor,body.plan_id)

@app.post('/api/subscriptions/{subscription_id}/archive')
def archive_subscription(subscription_id:int,request:Request,user=Depends(current_user)):
    csrf(request)
    from .service_state import effective_status
    with transaction() as conn:
        row=conn.execute('SELECT * FROM subscriptions WHERE id=%s AND user_id=%s FOR UPDATE',(subscription_id,user['id'])).fetchone()
        if not row: raise HTTPException(404,'Subscription not found')
        if effective_status(row) not in ('expired','cancelled','provisioning_failed'):
            raise HTTPException(409,'Only ended services can be removed')
        conn.execute('UPDATE subscriptions SET archived_at=now() WHERE id=%s',(subscription_id,))
    return {'ok':True}

@app.get('/api/subscriptions/{subscription_id}/renewal-options')
def renewal_options(subscription_id:int,payment_method:str='stars',user=Depends(current_user)):
    if payment_method not in ('wallet','stars'):
        raise HTTPException(403,'This payment method is unavailable in Telegram')
    from .renewal import options
    with transaction() as conn:
        return options(conn,user['id'],subscription_id,payment_method)

@app.get('/api/admin/renewals')
def admin_renewals(admin=Depends(require_admin)):
    with transaction() as conn:
        return conn.execute("""SELECT o.id,j.id AS job_id,o.id AS order_id,o.renewal_of_sub_id AS subscription_id,
          COALESCE(j.status,o.status) AS status,j.previous_expires_at,j.target_expires_at,
          j.previous_traffic_limit_bytes,j.target_traffic_limit_bytes,j.added_traffic_bytes,j.duration_hours,j.renewal_mode,
          j.prepared_at,o.created_at,o.user_id,o.price_minor,o.currency,o.payment_method,p.status AS payment_status
          FROM orders o LEFT JOIN renewal_jobs j ON j.order_id=o.id JOIN payments p ON p.order_id=o.id
          WHERE o.renewal_of_sub_id IS NOT NULL ORDER BY o.id DESC LIMIT 100""").fetchall()

@app.post("/api/assistant")
def assistant(body:Ask,request:Request,user=Depends(current_user)):
    csrf(request)
    return {"answer":answer(user["id"],body.question)}

@app.get("/api/admin/overview")
def overview(admin=Depends(require_admin)):
    with transaction() as conn:
        counts={name:conn.execute("SELECT count(*) AS n FROM "+name).fetchone()["n"] for name in ("users","orders","subscriptions","servers","provisioning_jobs","alerts")}
        jobs=conn.execute("SELECT j.id,j.status,j.attempts,j.last_error FROM provisioning_jobs j WHERE j.status IN ('retrying','failed') ORDER BY j.id DESC LIMIT 20").fetchall()
        alerts=conn.execute("SELECT id,title,severity,status,opened_at FROM alerts WHERE status<>'resolved' ORDER BY id DESC LIMIT 30").fetchall()
        servers=conn.execute("SELECT s.id,s.name,s.status,s.panel_id,m.health,m.cpu_percent,m.ram_percent,m.swap_percent,m.disk_percent,m.uptime_seconds,m.sampled_at FROM servers s LEFT JOIN LATERAL (SELECT * FROM server_metrics WHERE server_id=s.id ORDER BY sampled_at DESC LIMIT 1) m ON true ORDER BY s.id").fetchall()
    return {"version":Path("VERSION").read_text().strip(),"counts":counts,"jobs":jobs,"alerts":alerts,"servers":servers}

@app.post("/api/admin/wallet/deposit")
def deposit(body:Deposit,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        result=ledger(conn,body.user_id,body.amount_minor,"deposit","admin:"+body.reference)
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id,detail) VALUES (%s,'wallet.deposit','user',%s,%s)",(admin["id"],body.user_id,json.dumps({"reference":body.reference,"amount_minor":body.amount_minor})))
    return result

@app.post("/api/admin/alerts/ack")
def acknowledge(body:Ack,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        row=conn.execute("UPDATE alerts SET status='acknowledged',acknowledged_at=now() WHERE id=%s AND status='open' RETURNING id",(body.alert_id,)).fetchone()
        if not row:
            raise HTTPException(404,"Open alert not found")
        conn.execute("INSERT INTO alert_events(alert_id,action,actor_id) VALUES (%s,'acknowledged',%s)",(body.alert_id,admin["id"]))
    return {"ok":True}

@app.post("/api/admin/locations")
def add_location(body:LocationCreate,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        row=conn.execute("INSERT INTO locations(name) VALUES (%s) RETURNING id,name",(body.name,)).fetchone()
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,'create','location',%s)",(admin["id"],row["id"]))
    return row

@app.post("/api/admin/services")
def add_service(body:ServiceCreate,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        row=conn.execute("INSERT INTO services(name,description,trial_enabled) VALUES (%s,%s,%s) RETURNING id,name",(body.name,body.description,body.trial_enabled)).fetchone()
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,'create','service',%s)",(admin["id"],row["id"]))
    return row

@app.post("/api/admin/plans")
def add_plan(body:PlanCreate,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        row=conn.execute("INSERT INTO plans(service_id,name,duration_hours,price_minor) VALUES (%s,%s,%s,%s) RETURNING id,name",(body.service_id,body.name,body.duration_hours,body.price_minor)).fetchone()
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,'create','plan',%s)",(admin["id"],row["id"]))
    return row

@app.post("/api/admin/servers")
def add_server(body:ServerCreate,request:Request,admin=Depends(require_operator)):
    csrf(request)
    from .sanaei import validate_panel_url,encrypt_token
    try: safe_url=validate_panel_url(body.api_url)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    with transaction() as conn:
        row=conn.execute("INSERT INTO servers(location_id,name,api_url,api_token,api_token_ciphertext,capacity) VALUES (%s,%s,%s,'',%s,%s) RETURNING id,name",(body.location_id,body.name,safe_url,encrypt_token(body.api_token),body.capacity)).fetchone()
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,'create','server',%s)",(admin["id"],row["id"]))
    return row

@app.get("/app/")
def miniapp(): return FileResponse("web/app/index.html")
@app.get("/app/style.css")
def miniapp_css(): return FileResponse("web/app/style.css")
@app.get("/app/experience.css")
def miniapp_experience_css(): return FileResponse("web/app/experience.css")
@app.get("/app/app.js")
def miniapp_js(): return FileResponse("web/app/app.js")
@app.get("/assets/alart-mark.png")
def alart_mark(): return FileResponse("web/assets/alart-mark.png",media_type="image/png",headers={"Cache-Control":"public, max-age=86400"})
@app.get("/common/money.js")
def money_js(): return FileResponse("web/common/money.js")
@app.get("/common/i18n.js")
def i18n_js(): return FileResponse("web/common/i18n.js")
@app.get("/panel/")
def panel(): return FileResponse("web/panel/index.html")
@app.get("/panel/panel.js")
def panel_js(): return FileResponse("web/panel/panel.js")
@app.get("/panel/style.css")
def panel_css(): return FileResponse("web/panel/style.css")
@app.get("/panel/operations.css")
def panel_operations_css(): return FileResponse("web/panel/operations.css")
@app.get("/shop/")
def shop(): return FileResponse("web/shop/index.html")
@app.get("/shop/shop.js")
def shop_js(): return FileResponse("web/shop/shop.js")
