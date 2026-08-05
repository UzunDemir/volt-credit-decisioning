"""Create the Airflow metastore database (idempotent).

Runs as part of the ``airflow-init`` compose service, before ``airflow db migrate``.
"""

from __future__ import annotations

import os

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

PG_USER = os.environ.get("POSTGRES_USER", "volt")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "volt")
PG_HOST = os.environ.get("POSTGRES_HOST", "postgres")
AIRFLOW_DB = os.environ.get("AIRFLOW_DB_NAME", "volt_airflow")


def main() -> None:
    conn = psycopg2.connect(host=PG_HOST, user=PG_USER, password=PG_PASSWORD, dbname="volt_credit")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (AIRFLOW_DB,))
    if cur.fetchone():
        print(f"database {AIRFLOW_DB} already exists")
    else:
        cur.execute(f'CREATE DATABASE "{AIRFLOW_DB}"')
        print(f"database {AIRFLOW_DB} created")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
