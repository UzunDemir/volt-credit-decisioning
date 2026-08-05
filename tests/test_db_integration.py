"""Integration tests — require a running PostgreSQL (skipped otherwise).

Enable by exporting POSTGRES_HOST (and friends) before running pytest.
The full demo bootstrap (docker compose up) exercises the same path.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_HOST") is None,
    reason="POSTGRES_HOST not set — needs a live PostgreSQL",
)


def test_features_view_returns_expected_columns():
    from credit_decision.model.pipeline import FEATURE_COLUMNS, load_training_data

    df = load_training_data()
    assert len(df) > 0
    assert set(FEATURE_COLUMNS) <= set(df.columns)
    assert "has_default_12m" in df.columns


def test_etl_roundtrip_and_counts():
    from credit_decision.db import read_sql
    from credit_decision.etl import generate, load

    dfs = generate.drop_internal_columns(
        generate.generate_dataset(n_applications=300, seed=3)
    )
    load.load_dataframes(dfs, truncate=True)
    n_clients = read_sql("SELECT count(*) AS n FROM clients")["n"].iloc[0]
    n_txns = read_sql("SELECT count(*) AS n FROM transactions")["n"].iloc[0]
    assert n_clients == 300
    assert n_txns > 0


def test_decisions_roundtrip():
    from credit_decision.db import execute, read_sql

    execute(
        "INSERT INTO decisions (application_id, model_version, score, decision) "
        "VALUES (1, 1, 0.5, 'approve')"
    )
    df = read_sql("SELECT count(*) AS n FROM decisions")
    assert df["n"].iloc[0] >= 1
