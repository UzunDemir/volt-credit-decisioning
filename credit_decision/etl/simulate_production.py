"""Simulate production: generate monthly unlabeled batches and load them.

Months 0-2  -> "steady"   (business as usual)
Months 3+   -> "downturn" (income drop, unemployment up, cash-heavy mix — drift)

Usage:
    python -m credit_decision.etl.simulate_production --months 7 --n 5000
"""

from __future__ import annotations

import argparse
import time

from ..config import get_settings
from . import generate, load

BATCH_ID_SPACING = 10_000_000


def run(months: int = 7, n: int = 5_000, reset_production: bool = True) -> None:
    """Generate + load monthly production batches (2026-01 ..).

    Production batches APPEND to the training window (as real traffic would);
    ``reset_production`` first deletes any previously simulated batches so the
    run is idempotent. Production clients live in the id range
    [BATCH_ID_SPACING, ...) — the training window is never touched.
    """
    s = get_settings()
    t0 = time.time()
    if reset_production:
        from ..db import execute

        print("  clearing previous production batches ...")
        execute("DELETE FROM transactions WHERE client_id >= :lo", {"lo": BATCH_ID_SPACING})
        execute("DELETE FROM applications  WHERE client_id >= :lo", {"lo": BATCH_ID_SPACING})
        execute("DELETE FROM clients       WHERE client_id >= :lo", {"lo": BATCH_ID_SPACING})
    for i in range(months):
        scenario = "steady" if i < 3 else "downturn"
        dfs = generate.generate_production_batch(
            seed=s.data_seed, month_index=i, scenario=scenario,
            n=n,
            txn_min=s.data_n_txns_per_client_min,
            txn_max=s.data_n_txns_per_client_max,
            id_offset=(i + 1) * BATCH_ID_SPACING,  # keep production ids >= spacing
        )
        clean = generate.drop_internal_columns(
            {k: dfs[k] for k in ("clients", "applications", "transactions")}
        )
        load.load_dataframes(clean, truncate=False)
        print(f"  batch {i + 1}/{months} [{scenario}] {n:,} applications loaded")
    print(f"Production simulation finished in {time.time() - t0:.1f}s")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--months", type=int, default=7, help="number of monthly batches (2026-01 ..)")
    ap.add_argument("--n", type=int, default=5_000, help="applications per batch")
    args = ap.parse_args()
    run(months=args.months, n=args.n)


if __name__ == "__main__":
    main()
