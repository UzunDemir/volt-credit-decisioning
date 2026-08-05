"""Database access — SQLAlchemy engine + pandas read helpers."""

from functools import lru_cache

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import Settings, get_settings


@lru_cache
def get_engine(settings: Settings | None = None) -> Engine:
    s = settings or get_settings()
    return create_engine(s.postgres_url)


def read_sql(sql: str, params: dict | None = None, settings: Settings | None = None) -> pd.DataFrame:
    """Run SQL, return a DataFrame. Production feature engineering lives in SQL."""
    return pd.read_sql(text(sql), get_engine(settings), params=params)


def execute(sql: str, params: dict | None = None, settings: Settings | None = None) -> None:
    with get_engine(settings).begin() as conn:
        conn.execute(text(sql), params or {})
