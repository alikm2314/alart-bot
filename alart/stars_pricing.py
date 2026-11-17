"""Auditable XTR quotes derived from an operator-approved FX reference.

Telegram does not publish a universal per-user Stars purchase rate through the
Bot API.  We therefore never infer a user's store price.  The operator can
refresh a narrowly scoped USD/IRR reference, review it in the panel, and set
the Stars-per-USD and margin used for Alart invoices.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import json
import httpx
from fastapi import HTTPException

RATE_URL = "https://raw.githubusercontent.com/rate-json/default/main/data.json"
REFRESH_SECONDS = 5 * 60

def _config(row):
    value = (row or {}).get("config") if row else None
    if isinstance(value, str):
        try: value=json.loads(value)
        except ValueError: value={}
    return value if isinstance(value, dict) else {}

def settings(conn):
    """Return enabled reference settings, or None to retain legacy XTR prices."""
    try:
        row=conn.execute("SELECT config FROM payment_settings WHERE provider='stars'",()).fetchone()
    except (AssertionError, KeyError, TypeError): # lightweight unit-test database doubles
        return None
    config=_config(row)
    if not config.get("market_pricing_enabled"):
        return None
    try:
        irr_per_usd=Decimal(str(config["irr_per_usd"]))
        # The panel accepts the operator's natural input: USD cost of one
        # Star.  Keep the old inverse setting readable for existing installs.
        usd_per_star=Decimal(str(config.get("usd_per_star") or (Decimal(1)/Decimal(str(config["stars_per_usd"])))))
        stars_per_usd=Decimal(1)/usd_per_star
        markup=Decimal(str(config.get("markup_percent",0)))
        updated=datetime.fromisoformat(str(config["rate_updated_at"]).replace("Z","+00:00"))
    except (KeyError, ValueError, ArithmeticError):
        raise HTTPException(409,"Live Stars pricing needs a refreshed USD/IRR reference")
    if irr_per_usd<=0 or usd_per_star<=0 or stars_per_usd<=0 or markup < 0 or markup > 500:
        raise HTTPException(409,"Live Stars pricing is invalid")
    if updated.tzinfo is None: updated=updated.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc)-updated.astimezone(timezone.utc)).total_seconds()>6*60*60:
        raise HTTPException(409,"The saved Stars reference is older than six hours; refresh it before checkout")
    return {"irr_per_usd":irr_per_usd,"stars_per_usd":stars_per_usd,"markup_percent":markup,
            "source":str(config.get("rate_source") or "operator reference"),
            "updated_at":config.get("rate_updated_at")}

def toman_to_stars(irr_amount: int, config: dict) -> int:
    if irr_amount <= 0: return 0
    usd=Decimal(irr_amount)/config["irr_per_usd"]
    stars=usd*config["stars_per_usd"]*(Decimal(1)+config["markup_percent"]/100)
    return int(stars.to_integral_value(rounding=ROUND_CEILING))

def refresh_reference(timeout: float=8) -> dict:
    """Fetch only the fixed HTTPS reference endpoint; no admin supplied URL."""
    try:
        response=httpx.get(RATE_URL,timeout=timeout,follow_redirects=False)
        response.raise_for_status()
        data=response.json()
        # The selected source publishes the Iranian open-market USD price in Toman.
        # Alart stores minor IRR units internally, so convert Toman to IRR once.
        rate=Decimal(str(data.get("values",{}).get("USD"))) * Decimal(10)
    except (httpx.HTTPError, ValueError, ArithmeticError, TypeError) as exc:
        raise RuntimeError("The FX reference is unavailable") from exc
    if rate<=0:
        raise RuntimeError("The FX reference returned an invalid IRR rate")
    return {"irr_per_usd":str(rate),"rate_source":"rate-json open-market USD/Toman reference",
            "rate_updated_at":datetime.now(timezone.utc).isoformat()}

def refresh_stored_reference(force: bool=False):
    """Refresh a configured reference at most once per five minutes.

    Failure retains the last verified rate and records the attempt, avoiding a
    thundering herd from customer quote requests.
    """
    from .db import transaction
    now=datetime.now(timezone.utc)
    with transaction() as conn:
        row=conn.execute("SELECT config FROM payment_settings WHERE provider='stars' FOR UPDATE").fetchone()
        config=_config(row)
        if not config.get("market_pricing_enabled"):
            return False
        try:
            checked=datetime.fromisoformat(str(config.get("rate_checked_at") or config.get("rate_updated_at")).replace("Z","+00:00"))
            if checked.tzinfo is None: checked=checked.replace(tzinfo=timezone.utc)
            if not force and (now-checked.astimezone(timezone.utc)).total_seconds()<REFRESH_SECONDS:
                return False
        except (ValueError, TypeError):
            pass
        config["rate_checked_at"]=now.isoformat()
        conn.execute("UPDATE payment_settings SET config=%s,updated_at=now() WHERE provider='stars'",(json.dumps(config),))
    try:
        reference=refresh_reference()
    except RuntimeError:
        return False
    with transaction() as conn:
        row=conn.execute("SELECT config FROM payment_settings WHERE provider='stars' FOR UPDATE").fetchone()
        config=_config(row)
        if not config.get("market_pricing_enabled"):
            return False
        config.update(reference)
        config["rate_checked_at"]=now.isoformat()
        conn.execute("UPDATE payment_settings SET config=%s,updated_at=now() WHERE provider='stars'",(json.dumps(config),))
    return True
