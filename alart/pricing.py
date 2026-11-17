"""All catalog prices and custom limits are calculated on the server."""
from dataclasses import dataclass
from fastapi import HTTPException
from .stars_pricing import settings as stars_settings, toman_to_stars

@dataclass(frozen=True)
class PriceQuote:
    plan_id: int
    service_id: int
    category: str
    users: int
    traffic_gb: int | None
    duration_days: int | None
    listed_minor: int
    discount_minor: int
    final_minor: int
    currency: str
    coupon_id: int | None
    free_purchase_enabled: bool
    pricing_reference: dict | None = None
    duration_hours: int | None = None
    toman_listed_minor: int | None = None
    toman_discount_minor: int | None = None
    toman_final_minor: int | None = None

    @property
    def selection(self):
        result={"users":self.users,"traffic_gb":self.traffic_gb,"duration_days":self.duration_days,"category":self.category}
        if self.duration_hours is not None: result['duration_hours']=self.duration_hours
        return result

def quote(conn, *, plan_id: int, payment_method: str, users: int | None = None, traffic_gb: int | None = None, duration_days: int | None = None, coupon_code: str | None = None, lock_coupon: bool = False) -> PriceQuote:
    plan=conn.execute("SELECT p.*,s.category FROM plans p JOIN services s ON s.id=p.service_id WHERE p.id=%s AND p.enabled AND s.enabled",(plan_id,)).fetchone()
    if not plan:
        raise HTTPException(404,"Plan unavailable")
    if payment_method not in ("wallet","stars","nowpayments","manual"):
        raise HTTPException(422,"Unsupported payment method")
    currency={"wallet":"IRR","stars":"XTR","nowpayments":"USD","manual":"IRR"}[payment_method]
    live_stars=stars_settings(conn) if payment_method=="stars" else None
    source_method="wallet" if live_stars else payment_method
    if plan["is_custom"]:
        rule=conn.execute("SELECT * FROM pricing_rules WHERE service_id=%s AND enabled",(plan["service_id"],)).fetchone()
        if not rule:
            raise HTTPException(409,"Custom pricing unavailable")
        user_count=users if users is not None else 1
        if not isinstance(user_count,int) or not rule["min_users"]<=user_count<=rule["max_users"]:
            raise HTTPException(422,"User count outside allowed range")
        category=plan["category"]
        if category=="volume_unlimited" and plan["duration_hours"]!=0:
            raise HTTPException(409,"Unlimited-duration product is misconfigured")
        if category.startswith("volume"):
            if traffic_gb is None or not rule["min_traffic_gb"]<=traffic_gb<=rule["max_traffic_gb"]:
                raise HTTPException(422,"Traffic outside allowed range")
        elif traffic_gb is not None:
            raise HTTPException(422,"Traffic does not apply to this product")
        if category in ("volume_timed","unlimited"):
            if duration_days is None or not rule["min_days"]<=duration_days<=rule["max_days"]:
                raise HTTPException(422,"Duration outside allowed range")
        elif duration_days is not None:
            raise HTTPException(422,"Duration does not apply to this product")
        prefix={"wallet":"minor","manual":"minor","stars":"stars","nowpayments":"usd_cents"}[source_method]
        base_key={"minor":"base_minor","stars":"base_stars","usd_cents":"base_usd_cents"}[prefix]
        base=rule[base_key]
        if base is None:
            raise HTTPException(409,"Price not configured for this method")
        price=base+max(0,user_count-1)*rule[f"per_user_{prefix}"]+(traffic_gb or 0)*rule[f"per_gb_{prefix}"]+(duration_days or 0)*rule[f"per_day_{prefix}"]
    else:
        if any(v is not None for v in (users,traffic_gb,duration_days)):
            raise HTTPException(422,"This plan has fixed limits")
        user_count=plan["user_limit"]
        traffic_gb=plan["traffic_gb"]
        duration_days=(plan["duration_hours"]+23)//24 if plan["duration_hours"] else None
        price={"wallet":plan["price_minor"],"manual":plan["price_minor"],"stars":plan["stars_price"],"nowpayments":plan["usd_price_cents"]}[source_method]
        if price is None:
            raise HTTPException(409,"Price not configured for this method")
        if plan["category"]=="volume_unlimited" and plan["duration_hours"]!=0:
            raise HTTPException(409,"Unlimited duration product is misconfigured")
        if plan["category"]=="volume_timed" and not duration_days:
            raise HTTPException(409,"Timed volume product is misconfigured")
    if plan["category"]=="volume_timed" and duration_days==0:
        raise HTTPException(422,"Timed volume products require an expiration")
    if price<0:
        raise HTTPException(409,"Invalid configured price")
    coupon_id=None
    discount=0
    if coupon_code:
        suffix=" FOR UPDATE" if lock_coupon else ""
        coupon=conn.execute("SELECT id,discount_percent FROM coupons WHERE code=%s AND uses<max_uses AND (expires_at IS NULL OR expires_at>now())"+suffix,(coupon_code.upper(),)).fetchone()
        if not coupon:
            raise HTTPException(422,"Coupon unavailable")
        coupon_id=coupon["id"]
        discount=price*coupon["discount_percent"]//100
    final=price-discount
    # Keep the canonical catalog amount in IRR for presentation.  A Stars
    # invoice has a separate payable amount, but must never replace the
    # customer-facing Toman price in an order summary.
    toman_listed_minor=price
    toman_discount_minor=discount
    toman_final_minor=final
    reference=None
    if live_stars:
        # Discounts are applied to the Toman/IRR price before conversion. Each
        # returned amount is rounded upward independently so the invoice never
        # asks for a fractional Star.
        listed_stars=toman_to_stars(price,live_stars)
        final_stars=toman_to_stars(final,live_stars)
        discount=listed_stars-final_stars
        price=listed_stars
        final=final_stars
        reference={"source":live_stars["source"],"updated_at":live_stars["updated_at"],
                   "irr_per_usd":str(live_stars["irr_per_usd"]),"stars_per_usd":str(live_stars["stars_per_usd"]),
                   "markup_percent":str(live_stars["markup_percent"])}
    if payment_method in ("stars","nowpayments") and final<=0:
        raise HTTPException(422,"Amount is too small for this payment method")
    duration_hours=(duration_days or 0)*24 if plan['is_custom'] else plan['duration_hours']
    return PriceQuote(plan["id"],plan["service_id"],plan["category"],user_count,traffic_gb,duration_days,price,discount,final,currency,coupon_id,plan.get("free_purchase_enabled",False),reference,duration_hours,toman_listed_minor,toman_discount_minor,toman_final_minor)
