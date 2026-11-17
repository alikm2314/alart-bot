import time
from fastapi import HTTPException
from .db import transaction
from .sanaei import SanaeiProvider, PanelAuthenticationError, PanelError, encrypt_token, validate_panel_url

def sync_inbound_snapshot(conn, panel_id: int, discovered: list[dict]) -> int:
    """Persist a successful ordered Sanaei snapshot and remove missing rows."""
    remote_ids=[]
    for remote_order, inbound in enumerate(discovered):
        remote_ids.append(inbound["remote_id"])
        conn.execute("""INSERT INTO panel_inbounds(panel_id,remote_id,remark,protocol,remote_enabled,remote_order)
          VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (panel_id,remote_id) DO UPDATE SET
          remark=EXCLUDED.remark,protocol=EXCLUDED.protocol,remote_enabled=EXCLUDED.remote_enabled,
          remote_order=EXCLUDED.remote_order,last_seen_at=now()""",
          (panel_id,inbound["remote_id"],inbound["remark"],inbound["protocol"],inbound["remote_enabled"],remote_order))
    if remote_ids:
        deleted=conn.execute("DELETE FROM panel_inbounds WHERE panel_id=%s AND remote_id<>ALL(%s)",(panel_id,remote_ids))
    else:
        deleted=conn.execute("DELETE FROM panel_inbounds WHERE panel_id=%s",(panel_id,))
    return deleted.rowcount

def register_panel(*, name: str, base_url: str, token: str, location_id: int, capacity: int, actor_id: int):
    url=validate_panel_url(base_url)
    encrypted=encrypt_token(token)
    with transaction() as conn:
        panel=conn.execute("INSERT INTO managed_panels(name,base_url,credential_ciphertext) VALUES (%s,%s,%s) RETURNING id,name,health,enabled",(name,url,encrypted)).fetchone()
        conn.execute("INSERT INTO servers(location_id,name,api_url,api_token,capacity,status,panel_id) VALUES (%s,%s,%s,'',%s,'disabled',%s)",(location_id,name,url,capacity,panel["id"]))
        conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,'panel.create','panel',%s)",(actor_id,panel["id"]))
    return panel

def check_and_discover(panel_id: int, *, actor_id: int | None = None):
    with transaction() as conn:
        panel=conn.execute("SELECT * FROM managed_panels WHERE id=%s",(panel_id,)).fetchone()
    if not panel:
        raise HTTPException(404,"Panel not found")
    started=time.monotonic()
    try:
        discovered=SanaeiProvider.from_panel(panel).discover_inbounds()
        health="online"
        error=None
    except PanelAuthenticationError:
        discovered=[];health="authentication_failed";error="Authentication failed"
    except PanelError:
        discovered=[];health="offline";error="Panel unavailable"
    with transaction() as conn:
        conn.execute("UPDATE managed_panels SET health=%s,last_error=%s,latency_ms=%s,last_ok_at=CASE WHEN %s='online' THEN now() ELSE last_ok_at END WHERE id=%s",(health,error,int((time.monotonic()-started)*1000),health,panel_id))
        removed=sync_inbound_snapshot(conn,panel_id,discovered) if health=="online" else 0
        if actor_id:
            conn.execute("INSERT INTO audit_logs(actor_id,action,entity,entity_id) VALUES (%s,'panel.discover','panel',%s)",(actor_id,panel_id))
    return {"health":health,"discovered":len(discovered),"removed":removed,"error":error}

def public_panels(conn):
    return conn.execute("SELECT p.id,p.name,p.base_url,p.provider,p.enabled,p.health,p.latency_ms,p.last_ok_at,p.last_error,p.created_at,s.location_id,s.capacity FROM managed_panels p JOIN servers s ON s.panel_id=p.id ORDER BY p.id").fetchall()

def inbound_list(conn,panel_id:int):
    return conn.execute("SELECT id,remote_id,remark,protocol,remote_enabled,allowed,last_seen_at FROM panel_inbounds WHERE panel_id=%s AND remote_enabled ORDER BY remote_order,remote_id",(panel_id,)).fetchall()
