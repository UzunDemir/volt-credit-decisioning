"""Stack smoke test — no database required.

Verifies that every module imports and that the Evidently 0.7.x report
dict has the structure the monitoring job extracts (guards against
version drift in the Evidently API).

Usage:
    python scripts/smoke_stack.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd


def main() -> None:
    # ---- 1. all modules import ------------------------------------------
    import credit_decision.config  # noqa: F401
    import credit_decision.dashboard.app  # noqa: F401
    import credit_decision.db  # noqa: F401
    import credit_decision.etl.generate  # noqa: F401
    import credit_decision.etl.load  # noqa: F401
    import credit_decision.etl.run_etl  # noqa: F401
    import credit_decision.etl.simulate_production  # noqa: F401
    import credit_decision.experiments.ab_test  # noqa: F401
    import credit_decision.experiments.uplift  # noqa: F401
    import credit_decision.model.evaluate  # noqa: F401
    import credit_decision.model.forecast  # noqa: F401
    import credit_decision.model.pipeline  # noqa: F401
    import credit_decision.model.train  # noqa: F401
    import credit_decision.monitoring.run_monitor  # noqa: F401
    import credit_decision.serving.api  # noqa: F401
    import credit_decision.serving.model_loader  # noqa: F401
    print("imports: OK")

    # ---- 2. Evidently report structure (0.7.x) ---------------------------
    import json

    from evidently import Report
    from evidently.presets import DataDriftPreset, DataSummaryPreset

    from credit_decision.monitoring.run_monitor import _drift_summary

    rng = np.random.default_rng(0)
    ref = pd.DataFrame({
        "income": rng.lognormal(8.0, 0.4, 5_000),
        "util_income_30d": rng.beta(2, 5, 5_000),
        "night_share_90d": rng.beta(1.5, 8, 5_000),
        "score": rng.beta(2, 6, 5_000),
    })
    cur = ref.copy()
    cur["income"] = ref["income"] * 0.8            # drift
    cur["util_income_30d"] = ref["util_income_30d"] * 1.5

    report = Report(metrics=[DataDriftPreset()])
    snapshot = report.run(reference_data=ref, current_data=cur)
    payload = json.loads(snapshot.json())
    for m in payload["metrics"]:
        print("METRIC", m["metric_name"], "=>", json.dumps(m.get("value"))[:220])

    summary = _drift_summary(snapshot)
    assert "share_drifted" in summary, f"unexpected drift dict: {list(summary)}"
    assert summary["total_columns"] == 4, summary
    assert summary["drifted_columns"] >= 2, summary
    assert isinstance(summary["dataset_drift"], bool)
    print(f"evidently drift summary: OK (drifted {summary['drifted_columns']}/4, "
          f"dataset_drift={summary['dataset_drift']})")

    qreport = Report(metrics=[DataSummaryPreset()])
    qsnapshot = qreport.run(reference_data=ref, current_data=cur)
    Path("data/processed").mkdir(parents=True, exist_ok=True)
    qsnapshot.save_html("data/processed/smoke_quality.html")
    print("data summary: OK")

    # ---- 3. FastAPI app object -------------------------------------------
    from credit_decision.serving.api import app

    assert app.title == "Volt Credit Decisioning API"
    assert [r.path for r in app.routes if r.path.startswith("/v1")], "API routes missing"
    print("fastapi app: OK")

    # ---- 4. MLflow alias API (3.x) ---------------------------------------
    import mlflow
    from mlflow.tracking import MlflowClient

    assert hasattr(MlflowClient, "get_model_version_by_alias"), "alias API missing"
    assert hasattr(MlflowClient, "set_registered_model_alias"), "alias API missing"
    print(f"mlflow {mlflow.__version__} alias API: OK")

    print("STACK OK")


if __name__ == "__main__":
    main()
