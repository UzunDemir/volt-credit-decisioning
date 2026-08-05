"""Diagnostics: inspect MLflow registry model source + artifact URI.

Usage:
    set MLFLOW_TRACKING_URI=http://localhost:5000
    python scripts/mlflow_check.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mlflow  # noqa: E402
from mlflow.tracking import MlflowClient  # noqa: E402


def main() -> None:
    uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    mlflow.set_tracking_uri(uri)
    client = MlflowClient()

    try:
        mv = client.get_model_version_by_alias("credit_scorer", "production")
        print(f"version: {mv.version}")
        print(f"source:  {mv.source}")
        print(f"tags:    {mv.tags}")
        run = client.get_run(mv.run_id)
        print(f"run artifact_uri: {run.info.artifact_uri}")
    except Exception as exc:  # noqa: BLE001
        print(f"registry lookup failed: {exc}")
        return

    try:
        model = mlflow.sklearn.load_model("models:/credit_scorer@production")
        print(f"load OK: {type(model).__name__}")
    except Exception as exc:  # noqa: BLE001
        print(f"load FAILED: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
