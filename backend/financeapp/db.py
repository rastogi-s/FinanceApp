"""Database connection. Tables arrive with Phase 1; for now the app only checks Postgres is reachable."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine, text

from financeapp.config import get_settings


@lru_cache
def get_engine() -> Engine | None:
    url = get_settings().database_url
    if not url:
        return None
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, connect_args={"connect_timeout": 3})


def database_ok() -> bool:
    engine = get_engine()
    if engine is None:
        return False
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False
