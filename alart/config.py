import os
from functools import lru_cache

@lru_cache
def settings():
    keys = ("DATABASE_URL", "BOT_TOKEN", "ADMIN_TELEGRAM_ID", "SESSION_SECRET")
    missing = [key for key in keys if not os.getenv(key)]
    if missing:
        raise RuntimeError("Missing configuration: " + ", ".join(missing))
    if len(os.environ["SESSION_SECRET"]) < 32:
        raise RuntimeError("SESSION_SECRET must be at least 32 characters")
    return {key: os.getenv(key, "") for key in (*keys, "PROVISIONING_BASE_URL", "PROVISIONING_TOKEN", "OPENAI_API_KEY", "OPENAI_MODEL", "DOMAIN", "PANEL_ENCRYPTION_KEY", "BACKUP_ENCRYPTION_KEY", "NOWPAYMENTS_API_KEY", "NOWPAYMENTS_IPN_SECRET")}
