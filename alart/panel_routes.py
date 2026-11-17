import json
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from .auth import require_admin, require_operator, require_owner, csrf
from .db import transaction
from .panel_management import register_panel,check_and_discover,public_panels,inbound_list
from .sanaei import encrypt_token,validate_panel_url
from .sanaei import SanaeiProvider
from .config import settings

router=APIRouter(prefix="/api/admin")

class PanelCreate(BaseModel):
    name:str=Field(min_length=2,max_length=100)
    base_url:str=Field(min_length=12,max_length=500)
    api_token:str=Field(min_length=16,max_length=2000)
    location_id:int=Field(gt=0)
    capacity:int=Field(gt=0)

class PanelEdit(BaseModel):
    name:str|None=None
    base_url:str|None=None
    api_token:str|None=None
    capacity:int|None=None

class Toggle(BaseModel):
    enabled:bool

class InboundAllowed(BaseModel):
    allowed:bool

class Mapping(BaseModel):
    inbound_ids:list[int]

class ProviderSettings(BaseModel):
    enabled:bool
    public_details:str=Field(default="",max_length=1000)
    pay_currency:str|None=Field(default=None,max_length=30)
    api_key:str|None=Field(default=None,max_length=2000)
    ipn_secret:str|None=Field(default=None,max_length=2000)
    market_pricing_enabled:bool=False
    usd_per_star:float|None=Field(default=None,gt=0,le=10)
    markup_percent:float=Field(default=0,ge=0,le=500)

class ProductCreate(BaseModel):
    name:str=Field(min_length=2,max_length=100)
    description:str=Field(default="",max_length=500)
    category:str=Field(pattern="^(unlimited|volume_unlimited|volume_timed)$")
    trial_enabled:bool=False

class ProductEdit(BaseModel):
    name:str|None=Field(default=None,min_length=2,max_length=100)
    description:str|None=Field(default=None,max_length=500)
    enabled:bool|None=None
    trial_enabled:bool|None=None

class PlanCreate(BaseModel):
    service_id:int=Field(gt=0)
    name:str=Field(min_length=2,max_length=100)
    duration_hours:int=Field(ge=0,le=8760)
    traffic_gb:int|None=Field(default=None,ge=0)
    user_limit:int=Field(default=1,ge=0,le=100)
    price_minor:int=Field(ge=0)
    stars_price:int|None=Field(default=None,gt=0)
    usd_price_cents:int|None=Field(default=None,gt=0)
    is_custom:bool=False
    free_purchase_enabled:bool=False

class PlanEdit(BaseModel):
    name:str|None=Field(default=None,min_length=2,max_length=100)
    duration_hours:int|None=Field(default=None,ge=0,le=8760)
    traffic_gb:int|None=Field(default=None,ge=0)
    user_limit:int|None=Field(default=None,ge=0,le=100)
    price_minor:int|None=Field(default=None,ge=0)
    stars_price:int|None=Field(default=None,gt=0)
    usd_price_cents:int|None=Field(default=None,gt=0)
    enabled:bool|None=None
    free_purchase_enabled:bool|None=None

class CustomVolumeOffer(BaseModel):
    category:str=Field(pattern="^(volume_unlimited|volume_timed)$")
    panel_id:int=Field(gt=0)
    rate_toman_per_gb:int=Field(gt=0,le=100000000)
    enabled:bool=True

class PricingRule(BaseModel):
    base_minor:int=Field(ge=0)
    per_user_minor:int=Field(ge=0)
    per_gb_minor:int=Field(ge=0)
    per_day_minor:int=Field(ge=0)
    base_stars:int|None=Field(default=None,gt=0)
    per_user_stars:int=Field(default=0,ge=0)
    per_gb_stars:int=Field(default=0,ge=0)
    per_day_stars:int=Field(default=0,ge=0)
    base_usd_cents:int|None=Field(default=None,gt=0)
    per_user_usd_cents:int=Field(default=0,ge=0)
    per_gb_usd_cents:int=Field(default=0,ge=0)
    per_day_usd_cents:int=Field(default=0,ge=0)
    min_users:int=Field(ge=0)
    max_users:int=Field(gt=0)
    min_traffic_gb:int=Field(ge=0)
    max_traffic_gb:int=Field(gt=0)
    min_days:int=Field(ge=0)
    max_days:int=Field(gt=0)
    enabled:bool=True

def audit(conn,actor,action,entity,entity_id):
    conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,%s,%s,%s)",(actor["id"],action,entity,entity_id))

@router.get("/custom-volume-offers")
def custom_volume_offers(admin=Depends(require_admin)):
    with transaction() as conn:
        return conn.execute("SELECT category,panel_id,rate_minor_per_gb,fixed_duration_days,enabled FROM custom_volume_offers ORDER BY category").fetchall()

@router.put("/custom-volume-offers")
def save_custom_volume_offer(body:CustomVolumeOffer,request:Request,admin=Depends(require_operator)):
    csrf(request)
    duration=0 if body.category=='volume_unlimited' else 30
    with transaction() as conn:
        panel=conn.execute("SELECT id FROM managed_panels WHERE id=%s AND enabled AND health='online'",(body.panel_id,)).fetchone()
        if not panel: raise HTTPException(422,"Choose an enabled online panel")
        existing=conn.execute("SELECT service_id FROM custom_volume_offers WHERE category=%s FOR UPDATE",(body.category,)).fetchone()
        if existing: service_id=existing['service_id']
        else:
            service_id=conn.execute("INSERT INTO services(name,description,category,enabled,internal) VALUES (%s,%s,%s,true,true) RETURNING id",('Internal custom volume '+body.category,'Internal custom offer',body.category)).fetchone()['id']
            conn.execute("INSERT INTO plans(service_id,name,duration_hours,traffic_gb,user_limit,price_minor,is_custom) VALUES (%s,%s,%s,0,0,0,true)",(service_id,'Custom volume',duration*24))
        conn.execute("UPDATE plans SET duration_hours=%s,enabled=%s WHERE service_id=%s AND is_custom",(duration*24,body.enabled,service_id))
        conn.execute("INSERT INTO pricing_rules(service_id,base_minor,per_user_minor,per_gb_minor,per_day_minor,min_users,max_users,min_traffic_gb,max_traffic_gb,min_days,max_days,enabled) VALUES (%s,0,0,%s,0,1,1,1,500,1,30,%s) ON CONFLICT(service_id) DO UPDATE SET per_gb_minor=EXCLUDED.per_gb_minor,min_traffic_gb=1,max_traffic_gb=500,min_days=1,max_days=30,enabled=EXCLUDED.enabled",(service_id,body.rate_toman_per_gb*10,body.enabled))
        inbound_ids=[row['id'] for row in conn.execute("SELECT id FROM panel_inbounds WHERE panel_id=%s AND allowed AND remote_enabled",(body.panel_id,)).fetchall()]
        if not inbound_ids: raise HTTPException(422,"Selected panel has no allowed inbounds")
        conn.execute("DELETE FROM service_inbound_mappings WHERE service_id=%s",(service_id,))
        for inbound_id in inbound_ids: conn.execute("INSERT INTO service_inbound_mappings(service_id,inbound_id) VALUES (%s,%s)",(service_id,inbound_id))
        conn.execute("INSERT INTO custom_volume_offers(category,service_id,panel_id,rate_minor_per_gb,fixed_duration_days,enabled,updated_at) VALUES (%s,%s,%s,%s,%s,%s,now()) ON CONFLICT(category) DO UPDATE SET panel_id=EXCLUDED.panel_id,rate_minor_per_gb=EXCLUDED.rate_minor_per_gb,fixed_duration_days=EXCLUDED.fixed_duration_days,enabled=EXCLUDED.enabled,updated_at=now()",(body.category,service_id,body.panel_id,body.rate_toman_per_gb*10,duration,body.enabled))
        audit(conn,admin,'custom_volume.save','custom_volume_offer',service_id)
    return {'ok':True,'service_id':service_id}

@router.get("/products/catalog")
def admin_catalog(admin=Depends(require_admin)):
    with transaction() as conn:
        return {
            "services":conn.execute("SELECT id,name,description,category,enabled,trial_enabled FROM services WHERE NOT internal AND enabled ORDER BY id").fetchall(),
            "plans":conn.execute("SELECT p.id,p.service_id,p.name,p.duration_hours,p.traffic_gb,p.user_limit,p.price_minor,p.stars_price,p.usd_price_cents,p.is_custom,p.enabled,p.free_purchase_enabled FROM plans p JOIN services s ON s.id=p.service_id WHERE p.enabled AND s.enabled AND NOT s.internal ORDER BY p.id").fetchall(),
            "locations":conn.execute("SELECT id,name,enabled FROM locations ORDER BY name").fetchall(),
        }

@router.get("/panels")
def panels(admin=Depends(require_admin)):
    with transaction() as conn:
        rows=public_panels(conn)
    return rows

@router.get("/panels/{panel_id}/nodes")
def panel_nodes(panel_id:int,admin=Depends(require_admin)):
    with transaction() as conn:
        panel=conn.execute("SELECT id,base_url,credential_ciphertext,enabled FROM managed_panels WHERE id=%s",(panel_id,)).fetchone()
    if not panel:
        raise HTTPException(404,"Panel not found")
    if not panel['enabled']:
        return {'nodes':[],'available':False}
    try:
        return {'nodes':SanaeiProvider.from_panel(panel).nodes_status(),'available':True}
    except Exception:
        return {'nodes':[],'available':False}

@router.get('/clients')
def clients(admin=Depends(require_admin)):
    """Owner client view: Alart records plus live Sanaei records when available."""
    with transaction() as conn:
        alart=conn.execute("""SELECT u.id,u.telegram_id,u.name,u.username,s.id AS subscription_id,s.status,
          s.traffic_used_bytes,s.traffic_limit_bytes,s.expires_at,s.user_limit,p.name AS plan_name,
          CASE WHEN p.price_minor=0 THEN 'trial' ELSE 'purchase' END AS kind
          FROM users u JOIN subscriptions s ON s.user_id=u.id JOIN orders o ON o.id=s.order_id
          JOIN plans p ON p.id=o.plan_id ORDER BY s.id DESC LIMIT 500""").fetchall()
        panels=conn.execute("SELECT * FROM managed_panels WHERE enabled ORDER BY id").fetchall()
    live=[]
    for panel in panels:
        try:
            live.append({'panel_id':panel['id'],'panel_name':panel['name'],'clients':SanaeiProvider.from_panel(panel).list_clients(),'error':None})
        except Exception:
            # Do not leak credentials, URLs, or remote errors into browser UI.
            live.append({'panel_id':panel['id'],'panel_name':panel['name'],'clients':[],'error':'Live client data is unavailable.'})
    return {'alart_clients':alart,'panel_clients':live}

@router.post("/panels")
def add_panel(body:PanelCreate,request:Request,admin=Depends(require_operator)):
    csrf(request)
    return register_panel(name=body.name,base_url=body.base_url,token=body.api_token,location_id=body.location_id,capacity=body.capacity,actor_id=admin["id"])

@router.patch("/panels/{panel_id}")
def edit_panel(panel_id:int,body:PanelEdit,request:Request,admin=Depends(require_operator)):
    csrf(request)
    updates=[];values=[]
    if body.name is not None: updates.append("name=%s");values.append(body.name)
    if body.base_url is not None: updates.append("base_url=%s");values.append(validate_panel_url(body.base_url))
    if body.api_token is not None: updates.append("credential_ciphertext=%s");values.append(encrypt_token(body.api_token))
    with transaction() as conn:
        if updates:
            row=conn.execute("UPDATE managed_panels SET "+",".join(updates)+" WHERE id=%s RETURNING id",(*values,panel_id)).fetchone()
            if not row: raise HTTPException(404,"Panel not found")
        if body.capacity is not None:
            conn.execute("UPDATE servers SET capacity=%s WHERE panel_id=%s",(body.capacity,panel_id))
        audit(conn,admin,"panel.edit","panel",panel_id)
    return {"ok":True}

@router.post("/panels/{panel_id}/discover")
def discover(panel_id:int,request:Request,admin=Depends(require_operator)):
    csrf(request)
    return check_and_discover(panel_id,actor_id=admin["id"])

@router.put("/panels/{panel_id}/enabled")
def panel_enabled(panel_id:int,body:Toggle,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        panel=conn.execute("SELECT health FROM managed_panels WHERE id=%s FOR UPDATE",(panel_id,)).fetchone()
        if not panel: raise HTTPException(404,"Panel not found")
        if body.enabled and panel["health"]!="online": raise HTTPException(409,"Test the panel connection before enabling")
        conn.execute("UPDATE managed_panels SET enabled=%s WHERE id=%s",(body.enabled,panel_id))
        conn.execute("UPDATE servers SET status=%s WHERE panel_id=%s",("enabled" if body.enabled else "disabled",panel_id))
        audit(conn,admin,"panel.enable" if body.enabled else "panel.disable","panel",panel_id)
    return {"enabled":body.enabled}

@router.get("/panels/{panel_id}/inbounds")
def inbounds(panel_id:int,admin=Depends(require_admin)):
    with transaction() as conn:
        return inbound_list(conn,panel_id)

@router.put("/inbounds/{inbound_id}/allowed")
def inbound_allowed(inbound_id:int,body:InboundAllowed,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        row=conn.execute("UPDATE panel_inbounds SET allowed=%s WHERE id=%s RETURNING id",(body.allowed,inbound_id)).fetchone()
        if not row: raise HTTPException(404,"Inbound not found")
        audit(conn,admin,"inbound.allowed","inbound",inbound_id)
    return {"allowed":body.allowed}

@router.put("/services/{service_id}/mappings")
def mappings(service_id:int,body:Mapping,request:Request,admin=Depends(require_operator)):
    csrf(request)
    ids=sorted(set(body.inbound_ids))
    with transaction() as conn:
        available=conn.execute("SELECT id,protocol FROM panel_inbounds WHERE id=ANY(%s) AND allowed AND remote_enabled",(ids,)).fetchall() if ids else []
        if len(available)!=len(ids): raise HTTPException(422,"One or more inbounds are not allowed or online")
        supported={"vless","vmess","trojan","shadowsocks","hysteria","hysteria2","mtproto"}
        if any(row["protocol"].lower() not in supported for row in available):
            raise HTTPException(422,"One or more inbounds cannot generate a supported client link")
        conn.execute("DELETE FROM service_inbound_mappings WHERE service_id=%s",(service_id,))
        for inbound_id in ids:
            conn.execute("INSERT INTO service_inbound_mappings(service_id,inbound_id) VALUES (%s,%s)",(service_id,inbound_id))
        audit(conn,admin,"service.map","service",service_id)
    return {"inbound_ids":ids}

@router.get("/services/{service_id}/mappings")
def get_mappings(service_id:int,admin=Depends(require_admin)):
    with transaction() as conn:
        return conn.execute("SELECT inbound_id FROM service_inbound_mappings WHERE service_id=%s ORDER BY inbound_id",(service_id,)).fetchall()

@router.post("/products")
def add_product(body:ProductCreate,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        row=conn.execute("INSERT INTO services(name,description,category,trial_enabled) VALUES (%s,%s,%s,%s) RETURNING id,name",(body.name,body.description,body.category,body.trial_enabled)).fetchone()
        audit(conn,admin,"product.create","service",row["id"])
    return row

@router.patch("/products/{service_id}")
def edit_product(service_id:int,body:ProductEdit,request:Request,admin=Depends(require_operator)):
    csrf(request)
    values=body.model_dump(exclude_unset=True,exclude_none=True)
    if not values: raise HTTPException(422,"No product changes supplied")
    with transaction() as conn:
        row=conn.execute("UPDATE services SET "+",".join(f"{key}=%s" for key in values)+" WHERE id=%s RETURNING id",(*values.values(),service_id)).fetchone()
        if not row: raise HTTPException(404,"Product not found")
        audit(conn,admin,"product.edit","service",service_id)
    return {"ok":True}

@router.delete("/products/{service_id}")
def delete_product(service_id:int,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        service=conn.execute("SELECT id FROM services WHERE id=%s FOR UPDATE",(service_id,)).fetchone()
        if not service: raise HTTPException(404,"Product not found")
        used=conn.execute("SELECT 1 FROM orders o JOIN plans p ON p.id=o.plan_id WHERE p.service_id=%s LIMIT 1",(service_id,)).fetchone()
        claimed=conn.execute("SELECT 1 FROM trial_claims WHERE service_id=%s LIMIT 1",(service_id,)).fetchone()
        if used or claimed:
            conn.execute("UPDATE services SET enabled=false,trial_enabled=false WHERE id=%s",(service_id,))
            conn.execute("UPDATE plans SET enabled=false WHERE service_id=%s",(service_id,))
            action='product.archive'
            outcome='archived'
        else:
            conn.execute("DELETE FROM service_inbound_mappings WHERE service_id=%s",(service_id,))
            conn.execute("DELETE FROM pricing_rules WHERE service_id=%s",(service_id,))
            conn.execute("DELETE FROM plans WHERE service_id=%s",(service_id,))
            conn.execute("DELETE FROM services WHERE id=%s",(service_id,))
            action='product.delete'
            outcome='deleted'
        audit(conn,admin,action,"service",service_id)
    return {"status":outcome}

@router.delete("/plans/{plan_id}")
def delete_plan(plan_id:int,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        plan=conn.execute("SELECT id,service_id FROM plans WHERE id=%s FOR UPDATE",(plan_id,)).fetchone()
        if not plan: raise HTTPException(404,"Plan not found")
        used=conn.execute("SELECT 1 FROM orders WHERE plan_id=%s LIMIT 1",(plan_id,)).fetchone()
        if used:
            conn.execute("UPDATE plans SET enabled=false WHERE id=%s",(plan_id,)); outcome='archived'; action='plan.archive'
        else:
            conn.execute("DELETE FROM plans WHERE id=%s",(plan_id,)); outcome='deleted'; action='plan.delete'
        audit(conn,admin,action,"plan",plan_id)
    return {"status":outcome}

@router.post("/products/plans")
def add_product_plan(body:PlanCreate,request:Request,admin=Depends(require_operator)):
    csrf(request)
    with transaction() as conn:
        row=conn.execute("INSERT INTO plans(service_id,name,duration_hours,traffic_gb,user_limit,price_minor,stars_price,usd_price_cents,is_custom,free_purchase_enabled) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,name",(body.service_id,body.name,body.duration_hours,body.traffic_gb,body.user_limit,body.price_minor,body.stars_price,body.usd_price_cents,body.is_custom,body.free_purchase_enabled)).fetchone()
        audit(conn,admin,"plan.create","plan",row["id"])
    return row

@router.patch("/plans/{plan_id}")
def edit_plan(plan_id:int,body:PlanEdit,request:Request,admin=Depends(require_operator)):
    csrf(request)
    values=body.model_dump(exclude_unset=True,exclude_none=True)
    if not values: raise HTTPException(422,"No plan changes supplied")
    with transaction() as conn:
        row=conn.execute("UPDATE plans SET "+",".join(f"{key}=%s" for key in values)+" WHERE id=%s RETURNING id",(*values.values(),plan_id)).fetchone()
        if not row: raise HTTPException(404,"Plan not found")
        audit(conn,admin,"plan.edit","plan",plan_id)
    return {"ok":True}

@router.put("/products/{service_id}/pricing")
def set_pricing(service_id:int,body:PricingRule,request:Request,admin=Depends(require_operator)):
    csrf(request)
    if body.max_users<body.min_users or body.max_traffic_gb<body.min_traffic_gb or body.max_days<body.min_days:
        raise HTTPException(422,"Invalid pricing limits")
    data=body.model_dump()
    keys=list(data)
    with transaction() as conn:
        conn.execute("INSERT INTO pricing_rules(service_id,"+",".join(keys)+") VALUES ("+",".join(["%s"]*(len(keys)+1))+") ON CONFLICT (service_id) DO UPDATE SET "+",".join(f"{k}=EXCLUDED.{k}" for k in keys),(service_id,*data.values()))
        audit(conn,admin,"pricing.update","service",service_id)
    return {"ok":True}

@router.get("/products/{service_id}/pricing")
def get_pricing(service_id:int,admin=Depends(require_admin)):
    with transaction() as conn:
        row=conn.execute("SELECT * FROM pricing_rules WHERE service_id=%s",(service_id,)).fetchone()
    if not row: raise HTTPException(404,"Pricing rule not found")
    return row

@router.put("/payments/{provider}/settings")
def payment_settings(provider:str,body:ProviderSettings,request:Request,admin=Depends(require_operator)):
    csrf(request)
    if provider not in ("stars","nowpayments","manual"): raise HTTPException(404,"Unknown provider")
    if provider=="nowpayments" and body.enabled and not body.pay_currency:
        raise HTTPException(422,"Choose a NOWPayments pay currency")
    if provider=="manual" and body.enabled and not body.public_details.strip():
        raise HTTPException(422,"Configure destination payment details")
    with transaction() as conn:
        encrypted=None
        if provider=="nowpayments":
            if (body.api_key is None)!=(body.ipn_secret is None):
                raise HTTPException(422,"Provide both NOWPayments secrets together")
            if body.api_key and body.ipn_secret:
                encrypted=encrypt_token(json.dumps({"api_key":body.api_key,"ipn_secret":body.ipn_secret}))
            current=conn.execute("SELECT credential_ciphertext FROM payment_settings WHERE provider='nowpayments'",()).fetchone()
            if body.enabled and not encrypted and not current["credential_ciphertext"] and not (settings()["NOWPAYMENTS_API_KEY"] and settings()["NOWPAYMENTS_IPN_SECRET"]):
                raise HTTPException(409,"NOWPayments API key and IPN secret are required")
        current=conn.execute("SELECT config FROM payment_settings WHERE provider=%s",(provider,)).fetchone()
        current_config=current["config"] if current else {}
        if isinstance(current_config,str):
            try: current_config=json.loads(current_config)
            except ValueError: current_config={}
        config={"pay_currency":body.pay_currency} if provider=="nowpayments" else dict(current_config or {})
        if provider=="stars":
            usd_per_star=body.usd_per_star or config.get("usd_per_star")
            if body.market_pricing_enabled and not usd_per_star:
                raise HTTPException(422,"Set the USD price of one Star before enabling market-based pricing")
            config.update({"market_pricing_enabled":body.market_pricing_enabled,"usd_per_star":usd_per_star,"markup_percent":body.markup_percent})
        conn.execute("UPDATE payment_settings SET enabled=%s,public_details=%s,config=%s,credential_ciphertext=COALESCE(%s,credential_ciphertext),updated_at=now() WHERE provider=%s",(body.enabled,body.public_details,json.dumps(config),encrypted,provider))
        audit(conn,admin,"payment.settings","payment_setting",None)
    return {"enabled":body.enabled}

@router.post("/payments/stars/refresh-rate")
def refresh_stars_rate(request:Request,admin=Depends(require_operator)):
    csrf(request)
    from .stars_pricing import refresh_reference
    try: reference=refresh_reference()
    except RuntimeError as exc: raise HTTPException(502,"FX reference is unavailable; the saved quote is unchanged") from exc
    with transaction() as conn:
        row=conn.execute("SELECT config FROM payment_settings WHERE provider='stars' FOR UPDATE").fetchone()
        config=row["config"] if row else {}
        if isinstance(config,str):
            try: config=json.loads(config)
            except ValueError: config={}
        config=dict(config or {}); config.update(reference)
        conn.execute("UPDATE payment_settings SET config=%s,updated_at=now() WHERE provider='stars'",(json.dumps(config),))
        audit(conn,admin,"payment.stars_rate.refresh","payment_setting",None)
    return {"source":reference["rate_source"],"updated_at":reference["rate_updated_at"],"irr_per_usd":reference["irr_per_usd"]}

@router.get("/payments/settings")
def list_payment_settings(admin=Depends(require_admin)):
    with transaction() as conn:
        return conn.execute("SELECT provider,enabled,public_details,config,(credential_ciphertext IS NOT NULL) AS has_credentials,updated_at FROM payment_settings ORDER BY provider").fetchall()

class AdminGrant(BaseModel):
    telegram_id:int=Field(gt=0)
    role:str=Field(pattern="^(operator|viewer)$")

class TermsContent(BaseModel):
    fa:str=Field(min_length=20,max_length=4000)
    en:str=Field(min_length=20,max_length=4000)

@router.get("/admins/lookup")
def admin_lookup(telegram_id:int,owner=Depends(require_owner)):
    with transaction() as conn:
        row=conn.execute("SELECT u.id,u.telegram_id,u.name,u.username,a.role FROM users u LEFT JOIN admins a ON a.user_id=u.id WHERE u.telegram_id=%s",(telegram_id,)).fetchone()
    if not row: raise HTTPException(404,"This user has not started the bot yet")
    return row

@router.get("/admins")
def list_admins(owner=Depends(require_owner)):
    with transaction() as conn:
        return conn.execute("SELECT u.id,u.telegram_id,u.name,u.username,a.role FROM admins a JOIN users u ON u.id=a.user_id ORDER BY CASE a.role WHEN 'owner' THEN 0 ELSE 1 END,u.id").fetchall()

@router.post("/admins")
def grant_admin(body:AdminGrant,request:Request,owner=Depends(require_owner)):
    csrf(request)
    with transaction() as conn:
        user=conn.execute("SELECT id FROM users WHERE telegram_id=%s FOR UPDATE",(body.telegram_id,)).fetchone()
        if not user: raise HTTPException(404,"This user has not started the bot yet")
        conn.execute("INSERT INTO admins(user_id,role) VALUES (%s,%s) ON CONFLICT (user_id) DO UPDATE SET role=EXCLUDED.role",(user['id'],body.role))
        audit(conn,owner,'admin.grant','user',user['id'])
    return {'ok':True}

@router.delete("/admins/{telegram_id}")
def revoke_admin(telegram_id:int,request:Request,owner=Depends(require_owner)):
    csrf(request)
    if telegram_id==owner['telegram_id']: raise HTTPException(409,"The owner role cannot be removed")
    with transaction() as conn:
        row=conn.execute("DELETE FROM admins USING users WHERE admins.user_id=users.id AND users.telegram_id=%s AND admins.role<>'owner' RETURNING admins.user_id",(telegram_id,)).fetchone()
        if not row: raise HTTPException(404,"Admin not found")
        audit(conn,owner,'admin.revoke','user',row['user_id'])
    return {'ok':True}

@router.get("/content/terms")
def get_terms(owner=Depends(require_owner)):
    with transaction() as conn:
        return conn.execute("SELECT fa,en,updated_at FROM bot_content WHERE content_key='terms'").fetchone()

@router.put("/content/terms")
def put_terms(body:TermsContent,request:Request,owner=Depends(require_owner)):
    csrf(request)
    with transaction() as conn:
        conn.execute("UPDATE bot_content SET fa=%s,en=%s,updated_by=%s,updated_at=now() WHERE content_key='terms'",(body.fa.strip(),body.en.strip(),owner['id']))
        audit(conn,owner,'content.terms.update','bot_content',None)
    return {'ok':True}
