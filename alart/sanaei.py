"""Sanaei 3x-ui client API adapter. No user-facing secrets or response logging."""
from __future__ import annotations

import time
import re
import ipaddress
import socket
from urllib.parse import quote, urlsplit
import httpx
from cryptography.fernet import Fernet, InvalidToken
from .config import settings

class PanelError(Exception):
    """A sanitized operator-visible panel failure."""

class PanelAuthenticationError(PanelError):
    pass

def client_name(username: str | None, telegram_id: int, user_id: int, sequence: int) -> str:
    if sequence<1 or user_id<1:
        raise ValueError("Invalid client sequence")
    prefix=re.sub(r"[^a-zA-Z0-9_-]","",username or "")[:30] or f"user-{telegram_id}"
    return f"{prefix}-u{user_id}-{sequence:03d}"

def validate_panel_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or
        parsed.query or parsed.fragment or any(ord(char)<32 for char in value) or
        "/../" in parsed.path or "/./" in parsed.path):
        raise ValueError("Panel URL must be an HTTPS origin or path without credentials")
    host=parsed.hostname.rstrip(".").lower()
    if host=="localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Panel hostname must be publicly reachable")
    try:
        address=ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global: raise ValueError("Panel address must be publicly reachable")
    return value.rstrip("/")

def _check_public_dns(hostname: str):
    try:
        addresses=socket.getaddrinfo(hostname,None,type=socket.SOCK_STREAM)
    except OSError as exc:
        raise PanelError("Panel hostname could not be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise PanelError("Panel hostname resolves to a private or reserved address")

def _fernet() -> Fernet:
    key=settings()["PANEL_ENCRYPTION_KEY"]
    if not key:
        raise RuntimeError("PANEL_ENCRYPTION_KEY is required before adding panels")
    return Fernet(key.encode())

def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()

def decrypt_token(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise PanelError("Panel credential cannot be decrypted") from exc

def legacy_server_token(server:dict) -> str:
    if server.get("api_token_ciphertext"):
        return decrypt_token(server["api_token_ciphertext"])
    if server.get("api_token"):
        return server["api_token"]
    raise PanelError("Server credential is unavailable")

class SanaeiProvider:
    def __init__(self, base_url: str, token: str, *, client: httpx.Client | None = None):
        self.base_url=validate_panel_url(base_url)
        self.token=token
        self._client=client

    @classmethod
    def from_panel(cls, panel: dict) -> "SanaeiProvider":
        return cls(panel["base_url"],decrypt_token(panel["credential_ciphertext"]))

    def _request(self, method: str, path: str, body: dict | None = None, *, timeout: int = 15):
        url=self.base_url+"/panel/api/"+path.lstrip("/")
        headers={"Authorization":"Bearer "+self.token}
        try:
            if self._client:
                response=self._client.request(method,url,json=body,headers=headers,timeout=timeout)
            else:
                _check_public_dns(urlsplit(self.base_url).hostname)
                with httpx.Client(timeout=timeout,follow_redirects=False,trust_env=False) as client:
                    response=client.request(method,url,json=body,headers=headers)
            if response.status_code in (401,403):
                raise PanelAuthenticationError("Panel authentication failed")
            response.raise_for_status()
            data=response.json()
        except PanelError:
            raise
        except (httpx.HTTPError,ValueError) as exc:
            raise PanelError("Panel API unavailable or returned invalid data") from exc
        if not isinstance(data,dict) or data.get("success") is not True:
            raise PanelError("Panel rejected the operation")
        return data.get("obj")

    def discover_inbounds(self) -> list[dict]:
        rows=self._request("GET","inbounds/options")
        if not isinstance(rows,list):
            raise PanelError("Panel returned an invalid inbound list")
        # This list is an authoritative snapshot. Reject malformed data rather
        # than silently omitting it, which could delete a valid local inbound.
        result=[]
        seen=set()
        for row in rows:
            if not isinstance(row,dict) or type(row.get("id")) is not int or row["id"]<=0:
                raise PanelError("Panel returned an invalid inbound entry")
            if row["id"] in seen:
                raise PanelError("Panel returned duplicate inbound IDs")
            seen.add(row["id"])
            result.append({"remote_id":row["id"],"remark":str(row.get("remark") or row["id"])[:160],"protocol":str(row.get("protocol") or "unknown")[:40],"remote_enabled":bool(row.get("enable",True))})
        return result

    def server_status(self) -> dict:
        """Read the panel host's real system snapshot; absent values stay null."""
        data=self._request("GET","server/status",timeout=8)
        if not isinstance(data,dict):
            raise PanelError("Panel returned invalid server status")
        def percent(value):
            return round(value,2) if type(value) in (int,float) and 0<=value<=100 else None
        def ratio(value):
            if not isinstance(value,dict): return None
            used,total=value.get('current'),value.get('total')
            if type(used) not in (int,float) or type(total) not in (int,float) or total<=0 or used<0 or used>total:
                return None
            return round(100*used/total,2)
        uptime=data.get('uptime')
        return {'cpu_percent':percent(data.get('cpu')),'ram_percent':ratio(data.get('mem')),
                'swap_percent':ratio(data.get('swap')),'disk_percent':ratio(data.get('disk')),
                'uptime_seconds':uptime if type(uptime) is int and uptime>=0 else None,
                'tcp_count':data.get('tcpCount') if type(data.get('tcpCount')) is int and data['tcpCount']>=0 else None,
                'xray_state':str(data.get('xray',{}).get('state') or '')[:32] if isinstance(data.get('xray'),dict) else None}

    def nodes_status(self) -> list[dict]:
        rows=self._request("GET","nodes/list",timeout=8)
        if not isinstance(rows,list): raise PanelError("Panel returned invalid node list")
        result=[]
        for row in rows[:100]:
            if not isinstance(row,dict) or type(row.get('id')) is not int: continue
            def pct(key):
                value=row.get(key)
                return round(value,2) if type(value) in (int,float) and 0<=value<=100 else None
            result.append({'remote_id':row['id'],'name':str(row.get('name') or row['id'])[:100],
                'status':str(row.get('status') or 'unknown')[:30], 'cpu_percent':pct('cpuPct'),
                'ram_percent':pct('memPct'),'uptime_seconds':row.get('uptimeSecs') if type(row.get('uptimeSecs')) is int and row['uptimeSecs']>=0 else None,
                'online_count':row.get('onlineCount') if type(row.get('onlineCount')) is int and row['onlineCount']>=0 else None,
                'xray_state':str(row.get('xrayState') or '')[:32]})
        return result

    def get_client(self,email: str) -> dict | None:
        try:
            result=self._request("GET","clients/get/"+quote(email,safe=""))
        except PanelError:
            # The API uses success:false for unknown clients. Confirm through a list
            # before treating a failure as absence, so auth/network failures never
            # trigger duplicate creation.
            rows=self._request("GET","clients/list")
            if isinstance(rows,list) and not any(isinstance(x,dict) and x.get("email")==email for x in rows):
                return None
            raise
        if result is None:
            return None
        if not isinstance(result,dict):
            raise PanelError("Panel returned invalid client data")
        return result.get("client",result)

    def list_clients(self) -> list[dict]:
        """Return only fields that the Sanaei API actually reports.

        Online state and speeds are deliberately nullable: different Sanaei
        versions do not expose them consistently, and Alart must never infer
        that a client is online from a traffic counter.
        """
        rows=self._request("GET","clients/list")
        if not isinstance(rows,list):
            raise PanelError("Panel returned an invalid client list")
        result=[]
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('email'),str):
                raise PanelError("Panel returned an incomplete client list")
            def number(name):
                value=row.get(name)
                return value if type(value) in (int,float) and value>=0 else None
            traffic=row.get('traffic') if isinstance(row.get('traffic'),dict) else {}
            def traffic_number(name):
                value=traffic.get(name)
                return value if type(value) in (int,float) and value>=0 else None
            online=row.get('online') if isinstance(row.get('online'),bool) else None
            result.append({'email':row['email'][:160],'group':str(row.get('group'))[:80] if isinstance(row.get('group'),str) else None,'enabled':row.get('enable') if isinstance(row.get('enable'),bool) else None,
                'up_bytes':traffic_number('up'),'down_bytes':traffic_number('down'),'total_bytes':number('totalGB'),
                'expiry_ms':number('expiryTime'),'ip_limit':number('limitIp'),'online':online,
                'upload_bps':number('upSpeed'),'download_bps':number('downSpeed')})
        return result

    def create_client(self, *, email: str, telegram_id: int, inbound_ids: list[int], traffic_bytes: int | None, expires_at_ms: int | None, user_limit: int) -> dict:
        existing=self.get_client(email)
        if existing:
            if int(existing.get("tgId") or 0)!=telegram_id:
                raise PanelError("Client name already belongs to another Telegram account")
            return self._client_result(email,existing)
        payload={"client":{"email":email,"totalGB":traffic_bytes or 0,"expiryTime":expires_at_ms or 0,"tgId":telegram_id,"limitIp":user_limit,"enable":True},"inboundIds":inbound_ids}
        try:
            self._request("POST","clients/add",payload)
        except PanelError:
            # An ambiguous timeout may mean the remote write succeeded.
            existing=self.get_client(email)
            if not existing or int(existing.get("tgId") or 0)!=telegram_id:
                raise
        created=self.get_client(email)
        if not created:
            raise PanelError("Panel did not return the created client")
        return self._client_result(email,created)

    def _client_result(self,email: str,client: dict) -> dict:
        links=self._request("GET","clients/links/"+quote(email,safe=""))
        if not isinstance(links,list) or not links or not all(isinstance(x,str) for x in links):
            raise PanelError("Panel created a client but returned no usable links")
        subscription_url=next((client.get(key) for key in ("subscriptionUrl","subscription_url","subUrl","sub_url") if isinstance(client.get(key),str) and client.get(key).startswith("https://")),None)
        subscription_id=next((client.get(key) for key in ("subId","sub_id","subscriptionId","subscription_id") if isinstance(client.get(key),str) and client.get(key)),None)
        if not subscription_url and subscription_id:
            # Sanaei owns the public host, port and base path. The panel URL and
            # the protocol UUID are not subscription URLs.
            defaults=self._request("POST","setting/defaultSettings")
            if not isinstance(defaults,dict):
                raise PanelError("Panel returned invalid subscription settings")
            prefix=defaults.get('subURI')
            if defaults.get('subEnable') and isinstance(prefix,str) and prefix.startswith('https://'):
                subscription_url=prefix+quote(subscription_id,safe='')
        if subscription_url:
            try: validate_panel_url(subscription_url.split('?',1)[0])
            except ValueError as exc: raise PanelError("Panel returned an invalid subscription URL") from exc
        return {"remote_id":str(client.get("id") or client.get("uuid") or email),"configuration":"\n".join(links),"subscription_url":subscription_url}

    def disable_client(self,email: str):
        self._request("POST","clients/bulkDisable",{"emails":[email]})

    def renew_client(self,email: str, *, traffic_bytes: int | None, expires_at_ms: int | None, user_limit: int):
        client=self.get_client(email)
        if not client:
            raise PanelError("Existing client was not found")
        # ``clients/get`` contains database and UI-only fields (for example id,
        # createdAt and updatedAt).  The Sanaei update endpoint validates its
        # request schema and rejects that response object when it is posted back.
        # Send only the documented mutable, universal fields.  This also keeps a
        # renewal an absolute, idempotent update rather than a patch based on
        # stale counters.
        updated={
            "email":email,
            "totalGB":traffic_bytes or 0,
            "expiryTime":expires_at_ms or 0,
            "tgId":int(client.get("tgId") or 0),
            "limitIp":user_limit,
            "limitHwid":int(client.get("limitHwid") or 0),
            "enable":True,
        }
        # Keep the provider-issued subscription identity across renewals.
        for key in ('subId','flow','security','password','auth','group','comment'):
            if key in client:
                updated[key]=client[key]
        self._request("POST","clients/update/"+quote(email,safe=""),updated)
        renewed=self.get_client(email)
        if not renewed:
            raise PanelError("Panel did not return the renewed client")
        return self._client_result(email,renewed)

    def get_traffic(self,email: str) -> dict:
        result=self._request("GET","clients/traffic/"+quote(email,safe=""),timeout=5)
        if not isinstance(result,dict):
            raise PanelError("Panel returned invalid traffic data")
        if result.get("email") is not None and result["email"]!=email:
            raise PanelError("Panel returned traffic for another client")
        up,down=result.get("up"),result.get("down")
        if type(up) is not int or type(down) is not int or up<0 or down<0:
            raise PanelError("Panel returned invalid traffic counters")
        return {"up":up,"down":down,"used":up+down}
