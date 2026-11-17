"""Customer-visible state must never depend on a successful remote disable."""
from datetime import datetime, timezone


def effective_status(sub, now=None):
    current=sub['status']
    if current not in ('active','expiring'):
        return current
    if sub.get('remote_missing_at'):
        return 'expired'
    now=now or datetime.now(timezone.utc)
    expiry=sub.get('expires_at')
    if expiry and expiry<=now:
        return 'expired'
    limit=sub.get('traffic_limit_bytes')
    used=sub.get('traffic_used_bytes')
    if limit and used is not None and used>=limit:
        return 'expired'
    return current


def present(sub):
    sub=dict(sub)
    sub['status']=effective_status(sub)
    if sub['status'] not in ('active','expiring'):
        sub['configuration']=None
        sub['provider_subscription_url']=None
    return sub
