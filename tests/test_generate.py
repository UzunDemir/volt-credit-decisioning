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

def test_compute_labels_windows_use_own_application_date():
    """Regression: label windows must use the client's OWN applied_at.

    The old position-based reindex (_client_idx, 0-based) shifted every
    client onto its neighbour's application date and dropped client 1 —
    spenders right before THEIR application defaulted no more often than
    identical clients with no recent spend.
    """
    import numpy as np
    import pandas as pd

    from credit_decision.etl.generate import compute_labels

    n_pairs = 1_000
    rng = np.random.default_rng(0)
    n = 2 * n_pairs
    u = np.full(n, 0.30)  # same hidden risk for everyone

    spender_applied = pd.Timestamp("2025-01-10")
    quiet_applied = pd.Timestamp("2025-06-10")
    applied_at = np.where(
        np.arange(n) % 2 == 0, spender_applied, quiet_applied
    ).astype("datetime64[ns]")

    clients = pd.DataFrame({"client_id": np.arange(1, n + 1), "u": u})
    applications = pd.DataFrame({
        "client_id": np.arange(1, n + 1),
        "applied_at": applied_at,
        "income": 5000.0,
    })
    # each spender has one 10k$ outflow 5 days before ITS OWN application;
    # quiet clients have no transactions at all
    tx = pd.DataFrame({
        "client_id": np.arange(1, n + 1, 2),
        "txn_ts": spender_applied - pd.Timedelta(days=5),
        "amount": 10000.0,
        "_is_in": False,
        "_is_night": False,
        "_client_idx": np.arange(0, n, 2),
    })

    labeled = compute_labels(clients, applications, tx, rng)
    dr_spender = labeled[labeled["client_id"] % 2 == 1]["has_default_12m"].mean()
    dr_quiet = labeled[labeled["client_id"] % 2 == 0]["has_default_12m"].mean()
    assert dr_spender > dr_quiet + 0.05, (dr_spender, dr_quiet)
