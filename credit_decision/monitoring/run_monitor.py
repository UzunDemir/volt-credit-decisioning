"""Production monitoring job — Evidently drift & data-quality reports.

Pipeline:
  1. Optionally simulate production batches (--simulate): months 2026-01..07,
     "steady" for the first three, "downturn" (drift) afterwards.
  2. Reference window: labeled applications 2025-07..2025-12 (features + score).
  3. For every production month: compute features from the SQL view, score
     with the production model, run Evidently DataDrift + DataQuality.
  4. Persist summaries to ``monitoring_events`` (drives the dashboard) and
     save HTML reports under ``mlartifacts/monitoring/``.

Note on model-performance monitoring: production labels arrive with a 12-month
delay, so performance drift here is approximated by *score distribution drift*
plus feature drift — a deliberate, documented design choice.

Usage:
    python -m credit_decision.monitoring.run_monitor [--simulate] [--months 7]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import mlflow
import pandas as pd
from evidently import Report
from evidently.presets import DataDriftPreset, DataSummaryPreset

from ..config import get_settings
from ..db import execute, read_sql
from ..model.pipeline import FEATURE_COLUMNS
from ..serving.model_loader import get_production_model

REFERENCE_START = "2025-07-01"
REFERENCE_END = "2025-12-31"
REPORT_DIR = Path("mlartifacts/monitoring")


def _features_for_window(start: str, end: str) -> pd.DataFrame:
    return read_sql(
        "SELECT * FROM v_credit_features "
        "WHERE applied_at >= :start AND applied_at < :end ORDER BY application_id",
        {"start": start, "end": end},
    )


def _score(features: pd.DataFrame, model) -> pd.DataFrame:
    X = features[FEATURE_COLUMNS]
    out = features.copy()
    out["score"] = model.predict_proba(X)[:, 1]
    # label is None for production rows; drop it (and ids) from drift data
    out = out.drop(columns=[c for c in ("has_default_12m", "application_id", "client_id") if c in out.columns])
    return out


def _drift_summary(snapshot) -> dict:
    """Extract drift counts defensively from an Evidently snapshot dict.

    Evidently 0.7.x expands the DataDriftPreset into a `DriftedColumnsCount`
    metric plus one `ValueDrift(column=...)` metric per column — the parser
    below handles both without depending on preset internals.
    """
    payload = json.loads(snapshot.json())
    out: dict = {}
    drift_by: dict[str, float] = {}
    for metric in payload.get("metrics", []):
        name = metric.get("metric_name", "")
        value = metric.get("value")
        if name.startswith("DriftedColumnsCount"):
            out["drifted_columns"] = int(value.get("count", 0))
            out["share_drifted"] = float(value.get("share", 0.0))
        elif name.startswith("ValueDrift"):
            col = name.split("column=")[1].split(",")[0] if "column=" in name else name
            drift_by[col] = float(value) if isinstance(value, (int, float)) else 0.0
    out["total_columns"] = len(drift_by)
    # business-alert threshold: >30% of drifted columns warrants investigation
    out["dataset_drift"] = out.get("share_drifted", 0.0) >= 0.3
    out["top_drifted"] = sorted(drift_by.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return out


def _store_event(batch_name: str, report_type: str, drift_detected: bool, metrics: dict) -> None:
    execute(
        "INSERT INTO monitoring_events (batch_name, report_type, drift_detected, metrics) "
        "VALUES (:batch, :rtype, :drift, :metrics)",
        {"batch": batch_name, "rtype": report_type, "drift": drift_detected,
         "metrics": json.dumps(metrics)},
    )


def run_monitor(months: int = 7, simulate: bool = False, month: str | None = None) -> None:
    """Run drift/quality reports.

    ``month="2026-07"`` runs a single month (daily schedule); otherwise
    ``months`` batches starting at 2026-01 are processed (full run).
    """
    s = get_settings()

    if simulate:
        from ..etl import simulate_production  # local import keeps CLI light
        simulate_production.run(months=months, reset_production=True)

    mlflow.set_tracking_uri(s.mlflow_tracking_uri)
    prod = get_production_model(s)
    model = prod["model"]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    print("Building reference window (2025 H2, labeled) ...")
    reference = _score(_features_for_window(REFERENCE_START, REFERENCE_END), model)
    print(f"  reference rows: {len(reference):,}")

    month_range = [month] if month else [f"2026-{m + 1:02d}" for m in range(months)]

    with mlflow.start_run(run_name="monitoring"):
        for batch in month_range:
            start = f"{batch}-01"
            end = f"{batch}-28"
            current = _score(_features_for_window(start, end), model)
            if current.empty:
                print(f"  {batch}: no applications — skipping")
                continue
            print(f"  {batch}: {len(current):,} applications")

            score_stats = current["score"].describe(
                percentiles=[0.1, 0.5, 0.9]
            ).to_dict()

            drift_report = Report(metrics=[DataDriftPreset()])
            drift_snapshot = drift_report.run(reference_data=reference, current_data=current)
            summary = _drift_summary(drift_snapshot)
            summary["score"] = {k: float(v) for k, v in score_stats.items()}
            detected = bool(summary.get("dataset_drift", False))
            _store_event(batch, "data_drift", detected, summary)
            html = REPORT_DIR / f"drift_{batch}.html"
            drift_snapshot.save_html(str(html))
            mlflow.log_artifact(str(html))
            print(f"    drift_detected={detected} share={summary.get('share_drifted', 'n/a')}")

            quality_report = Report(metrics=[DataSummaryPreset()])
            quality_snapshot = quality_report.run(reference_data=reference, current_data=current)
            _store_event(batch, "data_quality", False, {})
            q_html = REPORT_DIR / f"quality_{batch}.html"
            quality_snapshot.save_html(str(q_html))
            mlflow.log_artifact(str(q_html))

    print("Monitoring finished. Reports in mlartifacts/monitoring/")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--simulate", action="store_true", help="load simulated production batches first")
    ap.add_argument("--months", type=int, default=7)
    ap.add_argument("--month", type=str, default=None,
                    help="single batch to monitor, e.g. 2026-07 (daily schedule)")
    args = ap.parse_args()
    run_monitor(months=args.months, simulate=args.simulate, month=args.month)


if __name__ == "__main__":
    main()
