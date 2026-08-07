"""Load the production model + business threshold from the MLflow registry.

The registry is the single source of truth: training promotes the champion
via the `production` alias and stores the cost-optimal threshold as a model
version tag. The API reads both — no duplicated config.

Caching: results are cached per tracking URI with a short TTL. A permanent
cache would pin the deployed model forever — a retrained champion or a new
shadow candidate would never be picked up without an API restart. The TTL
makes the registry the source of truth within a minute.
"""

from __future__ import annotations

import time

import mlflow
from mlflow.tracking import MlflowClient

from ..config import Settings, get_settings

MODEL_NAME = "credit_scorer"

_CACHE_TTL_SECONDS = 60.0

# key -> (monotonic timestamp, value); value may be None (no candidate)
_cache: dict = {}


def _cache_get(key: str) -> tuple[object, bool]:
    """Return (value, expired) — expired=True on a miss or after the TTL."""
    hit = _cache.get(key)
    if hit is None:
        return None, True
    ts, value = hit
    return value, time.monotonic() - ts > _CACHE_TTL_SECONDS


def _cache_put(key: str, value) -> None:
    _cache[key] = (time.monotonic(), value)


def get_production_model(settings: Settings | None = None) -> dict:
    """Load the production model, refreshing at most once per TTL window.

    Not thread-safe by design: a concurrent double-load is harmless (both
    threads build the same dict); the demo favours simplicity.
    """
    s = settings or get_settings()
    key = s.mlflow_tracking_uri
    value, expired = _cache_get(key)
    if not expired:
        return value
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
    _cache_put(key, result)
    return result


def get_candidate_model(settings: Settings | None = None) -> dict | None:
    """Load the shadow candidate (alias 'candidate'), or None if absent.

    Used for champion-challenger shadow scoring: the API scores with the
    champion and logs the candidate's probability next to it, so the two can
    be compared offline once labels arrive. The TTL cache re-checks the
    alias, so a candidate registered by drift_retrain is picked up without
    an API restart.
    """
    s = settings or get_settings()
    key = f"candidate:{s.mlflow_tracking_uri}"
    value, expired = _cache_get(key)
    if not expired:
        return value
    mlflow.set_tracking_uri(s.mlflow_tracking_uri)
    client = MlflowClient()
    try:
        mv = client.get_model_version_by_alias(MODEL_NAME, "candidate")
        model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@candidate")
    except Exception:  # noqa: BLE001 - no candidate / broken candidate -> shadow off
        _cache_put(key, None)
        return None
    result = {"model": model, "version": int(mv.version), "run_id": mv.tags.get("run_id", "")}
    _cache_put(key, result)
    return result
