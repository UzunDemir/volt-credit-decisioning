"""Load generated DataFrames into PostgreSQL via COPY (fast, no ORM overhead).

Column lists mirror sql/01_schema.sql. JSONB payloads arrive as JSON text;
PostgreSQL casts them natively during COPY.
"""

from __future__ import annotations

import io

import pandas as pd
import psycopg2

from ..config import Settings, get_settings

TABLES: dict[str, list[str]] = {
    "clients": ["client_id", "first_seen_at", "region", "segment"],
    "applications": [
        "application_id", "client_id", "applied_at", "amount", "term_months", "purpose",
        "income", "employment_status", "age", "credit_history_months", "num_open_loans",
        "has_default_12m",
    ],
    "transactions": ["txn_id", "client_id", "txn_ts", "amount", "direction", "category", "details"],
}


def _is_bool_col(s: pd.Series) -> bool:
    if s.dtype == bool:
        return True
    if s.dtype == object:
        uniq = s.dropna().unique()
        return len(uniq) > 0 and set(uniq) <= {True, False}
    return False


def _to_copy_buffer(df: pd.DataFrame, columns: list[str]) -> io.StringIO:
    """Serialize a DataFrame slice for COPY CSV.

    Booleans -> 't'/'f' (PostgreSQL literals), missing values -> empty field (NULL).
    """
    work = df[columns].copy()
    for col in columns:
        if _is_bool_col(work[col]):
            work[col] = work[col].map(lambda v: "" if pd.isna(v) else ("t" if v else "f"))
    buf = io.StringIO()
    work.to_csv(buf, index=False, header=False, na_rep="")
    buf.seek(0)
    return buf


def _psql_conn(settings: Settings | None = None):
    s = settings or get_settings()
    return psycopg2.connect(
        host=s.postgres_host, port=s.postgres_port,
        user=s.postgres_user, password=s.postgres_password, dbname=s.postgres_db,
    )


def load_dataframes(
    dfs: dict[str, pd.DataFrame],
    truncate: bool = True,
    settings: Settings | None = None,
) -> None:
    conn = _psql_conn(settings)
    try:
        with conn.cursor() as cur:
            if truncate:
                cur.execute("TRUNCATE clients, applications, transactions, decisions RESTART IDENTITY CASCADE")
            for table, columns in TABLES.items():
                buf = _to_copy_buffer(dfs[table], columns)
                col_list = ", ".join(columns)
                cur.copy_expert(f"COPY {table} ({col_list}) FROM STDIN WITH (FORMAT csv)", buf)
                print(f"  loaded {len(dfs[table]):,} rows into {table}")
        conn.commit()
    finally:
        conn.close()
