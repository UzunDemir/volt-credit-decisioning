"""ETL entrypoint: generate the seeded training window and load it into PostgreSQL.

Usage:
    python -m credit_decision.etl.run_etl

Deterministic: same env (DATA_SEED, DATA_N_APPLICATIONS) -> same database state.
"""

from __future__ import annotations

import time

from ..config import get_settings
from . import generate, load


def main() -> None:
    s = get_settings()
    t0 = time.time()

    print("Generating training window "
          f"(seed={s.data_seed}, applications={s.data_n_applications:,}) ...")
    dfs = generate.generate_dataset(
        seed=s.data_seed,
        n_applications=s.data_n_applications,
        txn_min=s.data_n_txns_per_client_min,
        txn_max=s.data_n_txns_per_client_max,
    )
    clean = generate.drop_internal_columns(dfs)

    apps = clean["applications"]
    print(f"  clients:      {len(clean['clients']):>10,}")
    print(f"  applications: {len(apps):>10,}")
    print(f"  transactions: {len(clean['transactions']):>10,}")
    print(f"  default rate: {apps['has_default_12m'].mean():.1%}")

    print("Loading into PostgreSQL ...")
    load.load_dataframes(clean)
    print(f"ETL finished in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
