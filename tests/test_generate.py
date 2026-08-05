"""Unit tests for the seeded data generator (no database required)."""

from __future__ import annotations

import json

from credit_decision.etl import generate

N = 800


def test_dataset_shapes_and_labels():
    dfs = generate.generate_dataset(n_applications=N, seed=1)
    assert len(dfs["clients"]) == N
    assert len(dfs["applications"]) == N
    assert dfs["applications"]["has_default_12m"].notna().all()
    assert dfs["applications"]["application_id"].is_unique


def test_default_rate_band():
    dfs = generate.generate_dataset(n_applications=N, seed=1)
    dr = dfs["applications"]["has_default_12m"].mean()
    assert 0.04 < dr < 0.25


def test_details_are_valid_json_with_expected_keys():
    dfs = generate.generate_dataset(n_applications=N, seed=1)
    sample = json.loads(dfs["transactions"]["details"].iloc[0])
    assert {"merchant", "channel", "mcc", "hour", "geo"} <= set(sample)
    assert isinstance(sample["geo"]["city"], str)


def test_risk_factor_correlates_with_label():
    dfs = generate.generate_dataset(n_applications=N, seed=1)
    meta = dfs["clients"].set_index("client_id")
    default_clients = dfs["applications"][dfs["applications"]["has_default_12m"]]["client_id"]
    assert meta.loc[default_clients, "u"].mean() > meta["u"].mean() + 0.05


def test_determinism_same_seed():
    a = generate.generate_dataset(n_applications=N, seed=7)
    b = generate.generate_dataset(n_applications=N, seed=7)
    assert a["applications"].equals(b["applications"])
    assert a["transactions"].equals(b["transactions"])


def test_downturn_shifts_distributions():
    steady = generate.generate_production_batch(n=N, month_index=0, scenario="steady")
    down = generate.generate_production_batch(n=N, month_index=4, scenario="downturn")
    assert down["applications"]["income"].mean() < steady["applications"]["income"].mean() * 0.95

    def cash_share(dfs):
        out = dfs["transactions"][dfs["transactions"]["direction"] == "out"]
        return (out["category"] == "cash").mean()

    assert cash_share(down) > cash_share(steady) * 1.3


def test_production_batch_unlabeled_and_id_offset():
    dfs = generate.generate_production_batch(n=N, month_index=1, id_offset=10_000_000)
    assert dfs["applications"]["has_default_12m"].isna().all()
    assert dfs["applications"]["application_id"].min() > 10_000_000
    assert dfs["clients"]["client_id"].min() > 10_000_000


def test_transaction_times_precede_application():
    dfs = generate.generate_dataset(n_applications=N, seed=1)
    app_ts = dfs["applications"].set_index("client_id")["applied_at"]
    tx = dfs["transactions"].head(5_000)
    assert (tx["txn_ts"] < tx["client_id"].map(app_ts)).all()
