"""Smoke test for the data generator — no database required.

Usage:
    python scripts/smoke_generate.py [--n 5000]

Asserts basic sanity (shapes, default rate band, no null ids, JSONB validity).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from credit_decision.etl import generate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5_000)
    args = ap.parse_args()

    t0 = time.time()
    dfs = generate.generate_dataset(n_applications=args.n)
    elapsed = time.time() - t0

    clients, apps, txns = dfs["clients"], dfs["applications"], dfs["transactions"]
    assert len(clients) == args.n, "clients count"
    assert len(apps) == args.n, "applications count"
    assert apps["application_id"].is_unique, "application ids unique"
    assert apps["has_default_12m"].notna().all(), "labels present"

    dr = apps["has_default_12m"].mean()
    assert 0.04 < dr < 0.25, f"default rate {dr:.1%} out of band"

    # JSONB payloads must parse and cover expected keys
    sample = json.loads(txns["details"].iloc[0])
    for key in ("merchant", "channel", "mcc", "hour", "geo"):
        assert key in sample, f"details missing {key}"
    assert txns["details"].map(json.loads).map(lambda d: isinstance(d["geo"]["city"], str)).all()

    # risk factor must correlate with label (else the model has nothing to learn)
    meta = clients.set_index("client_id")
    default_clients = apps[apps["has_default_12m"]]["client_id"]
    assert meta.loc[default_clients, "u"].mean() > meta["u"].mean() + 0.05, "u correlates with label"

    # ---- production-batch drift simulation must shift distributions ----
    steady = generate.generate_production_batch(n=2000, month_index=0, scenario="steady")
    down = generate.generate_production_batch(n=2000, month_index=4, scenario="downturn")
    assert down["applications"]["income"].mean() < steady["applications"]["income"].mean() * 0.95, \
        "downturn lowers incomes"

    def cash_share(dfs: dict) -> float:
        out = dfs["transactions"][dfs["transactions"]["direction"] == "out"]
        return float((out["category"] == "cash").mean())

    assert cash_share(down) > cash_share(steady) * 1.3, "downturn increases cash share"

    print(f"OK in {elapsed:.1f}s: {len(clients):,} clients, {len(apps):,} apps, "
          f"{len(txns):,} txns, default rate {dr:.1%}; drift simulation verified")


if __name__ == "__main__":
    main()
