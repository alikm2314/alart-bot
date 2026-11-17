from contextlib import contextmanager
import os
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
from .config import settings

_pool = None

def pool():
    global _pool
    if _pool is None:
        maximum=max(1,min(8,int(os.environ.get("DB_POOL_MAX","4"))))
        _pool = ConnectionPool(settings()["DATABASE_URL"], min_size=1, max_size=maximum, kwargs={"row_factory": dict_row})
    return _pool

@contextmanager
def transaction():
    with pool().connection() as conn:
        with conn.transaction():
            yield conn

def migrate():
    from pathlib import Path
    with transaction() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(9137001)")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY)")
        for file in sorted(Path("db").glob("*.sql")):
            if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (file.name,)).fetchone():
                conn.execute(file.read_text())
                conn.execute("INSERT INTO schema_migrations VALUES (%s)", (file.name,))
        # Existing legacy server credentials are encrypted in the same locked
        # transaction. Clear plaintext only after the ciphertext is written.
        from .sanaei import encrypt_token
        rows=conn.execute("SELECT id,api_token FROM servers WHERE api_token<>'' AND api_token_ciphertext IS NULL FOR UPDATE").fetchall()
        for row in rows:
            conn.execute("UPDATE servers SET api_token_ciphertext=%s,api_token='' WHERE id=%s",(encrypt_token(row["api_token"]),row["id"]))
