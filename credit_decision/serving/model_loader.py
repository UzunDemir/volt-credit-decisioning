"""Load the production model + business threshold from the MLflow registry.

The registry is the single source of truth: training promotes the champion
via the `production` alias and stores the cost-optimal threshold as a model
version tag. The API reads both — no duplicated config.
"""

from __future__ import annotations

import mlflow
from mlflow.tracking import MlflowClient

from ..config import Settings, get_settings

MODEL_NAME = "credit_scorer"

_cache: dict = {}


def get_production_model(settings: Settings | None = None) -> dict:
    """Load the production model once per process (Settings is unhashable, so
    the cache is a plain dict keyed by the tracking URI)."""
    s = settings or get_settings()
    if s.mlflow_tracking_uri in _cache:
        return _cache[s.mlflow_tracking_uri]
    mlflow.set_tracking_uri(s.mlflow_tracking_uri)
    client = MlflowClient()

    try:
        mv = client.get_model_version_by_alias(MODEL_NAME, "production")
    except Exception as exc:  # noqa: BLE001 — surfaced as a clear error
        raise RuntimeError(
            f"No '{MODEL_NAME}' model with alias 'production'. Run training first "
            "(python -m credit_decision.model.train)."
        ) from exc

    # serialization format (cloudpickle) is read from the model config;
    # mlflow 3.x skops audit does not apply to cloudpickle artifacts
    model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@production")
    threshold = float(mv.tags.get("threshold", s.model_approval_threshold))
    result = {
        "model": model,
        "version": int(mv.version),
        "run_id": mv.tags.get("run_id", ""),
        "threshold": threshold,
        "test_roc_auc": mv.tags.get("test_roc_auc", "n/a"),
        "champion": mv.tags.get("champion", "n/a"),
    }
    _cache[s.mlflow_tracking_uri] = result
    return result
