"""FastAPI serving layer — production credit decisioning API.

Endpoints:
  GET  /health              liveness probe
  GET  /model-info          deployed model version, threshold, test metrics
  POST /v1/score            score one application (features computed from DB)
  POST /v1/score-batch      score many application ids
  POST /v1/score-payload    score raw feature payloads (curl-friendly demo)
  GET  /v1/decisions/recent last N logged decisions

Every DB-backed scoring call is written to the ``decisions`` table —
the audit log that powers the business dashboard.
"""

from __future__ import annotations

import json
import logging
import threading
from contextlib import asynccontextmanager

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field

from ..db import execute, read_sql
from ..model.pipeline import FEATURE_COLUMNS
from .model_loader import get_candidate_model, get_production_model

logger = logging.getLogger("credit_decision.serving")


def _warmup(timeout: float = 30.0) -> None:
    """Load the production model in a daemon thread with a hard timeout.

    A dead/unreachable MLflow can make load_model *hang* rather than raise,
    which would block startup forever; a timed warm-up guarantees the API
    either starts warm or starts degraded.
    """
    outcome: dict = {}

    def _load() -> None:
        try:
            get_production_model()
            outcome["ok"] = True
        except Exception as exc:  # noqa: BLE001 - degraded mode
            outcome["error"] = str(exc)

    t = threading.Thread(target=_load, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        logger.warning("model warm-up timed out after %.0fs - serving in degraded mode", timeout)
    elif "error" in outcome:
        logger.warning("model unavailable at startup (%s) - serving in degraded mode", outcome["error"])


@asynccontextmanager
async def lifespan(_: FastAPI):
    _warmup()
    try:
        _ensure_schema()
    except Exception as exc:  # noqa: BLE001 — DB down at startup: fail open
        logger.warning("schema guard failed at startup (%s)", exc)
    yield


def _ensure_schema() -> None:
    """Idempotent runtime schema guard (source of truth: sql/01_schema.sql).

    Covers databases initialized before ``challenger_score`` /
    ``alert_events`` existed; fresh databases get both from the init scripts.
    """
    execute("ALTER TABLE decisions ADD COLUMN IF NOT EXISTS challenger_score NUMERIC(8,6)")
    execute(
        "CREATE TABLE IF NOT EXISTS alert_events ("
        " event_id BIGSERIAL PRIMARY KEY,"
        " title TEXT NOT NULL,"
        " message TEXT,"
        " state TEXT,"
        " labels JSONB,"
        " received_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )


app = FastAPI(
    title="Volt Credit Decisioning API",
    description="Credit scoring: features from SQL view -> champion model -> business decision.",
    version="0.1.0",
    lifespan=lifespan,
)

# Prometheus metrics: /metrics exposes RPS, latency histogram, error rate
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


class ScoreRequest(BaseModel):
    application_id: int = Field(gt=0)


class BatchScoreRequest(BaseModel):
    application_ids: list[int] = Field(min_length=1)


class PayloadScoreRequest(BaseModel):
    features: dict[str, float | int | str | None]
    application_id: int | None = None


class AlertPayload(BaseModel):
    title: str
    message: str = ""
    state: str = "firing"
    labels: dict = {}


class DecisionResponse(BaseModel):
    application_id: int
    model_version: str | int   # "fallback-rule" in degraded mode
    score: float
    decision: str
    threshold: float


def _features_for_application(application_id: int) -> pd.DataFrame:
    df = read_sql(
        "SELECT * FROM v_credit_features WHERE application_id = :id",
        {"id": application_id},
    )
    if df.empty:
        raise HTTPException(status_code=404, detail=f"application_id {application_id} not found")
    return df


def _fallback_score(features: pd.DataFrame) -> pd.Series:
    """Rule-based fallback when the production model is unreachable.

    A conservative approximation of the champion (utilization, night spend,
    leverage) so the API degrades gracefully instead of failing.
    """
    util = np.clip(features["util_income_30d"].fillna(0.5), 0.0, 3.0)
    night = np.clip(features["night_share_90d"].fillna(0.1), 0.0, 1.0)
    loans = np.clip(features["num_open_loans"].fillna(1), 0, 8)
    logit = -3.2 + 1.5 * util + 1.2 * night + 0.35 * loans
    return pd.Series(1.0 / (1.0 + np.exp(-logit)), index=features.index)


def _score_df(features: pd.DataFrame) -> pd.DataFrame:
    """Attach score + decision to a feature frame.

    Degraded mode: if the production model is unreachable (MLflow down,
    alias missing), fall back to a simple rule and mark the decision with
    model_version='fallback-rule'. Scoring must never fail end-to-end.
    """
    # score-payload calls may arrive without an application_id
    out = (features[["application_id"]].copy()
           if "application_id" in features.columns
           else pd.DataFrame(index=features.index))
    out["challenger_score"] = np.nan
    try:
        prod = get_production_model()
        X = features[FEATURE_COLUMNS]
        out["score"] = prod["model"].predict_proba(X)[:, 1]
        out["threshold"] = prod["threshold"]
        out["model_version"] = prod["version"]
        # champion-challenger shadow scoring: candidate scores alongside the
        # champion for offline comparison (labels arrive with a 12-month lag)
        cand = get_candidate_model()
        if cand is not None:
            out["challenger_score"] = cand["model"].predict_proba(X)[:, 1]
    except Exception as exc:  # noqa: BLE001 - degraded mode must not break scoring
        logger.warning("production model unavailable (%s) - using fallback rule", exc)
        out["score"] = _fallback_score(features)
        out["threshold"] = 0.157
        out["model_version"] = "fallback-rule"
    # approve LOW-risk applicants: score = P(default) <= threshold
    out["decision"] = np.where(out["score"] <= out["threshold"], "approve", "decline")
    return out


def _log_decisions(scored: pd.DataFrame) -> None:
    for _, row in scored.iterrows():
        chall = row.get("challenger_score")
        execute(
            "INSERT INTO decisions (application_id, model_version, score, decision, challenger_score) "
            "VALUES (:app, :ver, :score, :decision, :chall)",
            {
                "app": int(row["application_id"]),
                "ver": str(row["model_version"]),
                "score": float(row["score"]),
                "decision": row["decision"],
                "chall": None if chall is None or pd.isna(chall) else float(chall),
            },
        )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/model-info")
def model_info() -> dict:
    try:
        prod = get_production_model()
        return {
            "model": "credit_scorer",
            "version": prod["version"],
            "champion": prod["champion"],
            "threshold": prod["threshold"],
            "test_roc_auc": prod["test_roc_auc"],
            "run_id": prod["run_id"],
        }
    except Exception as exc:  # noqa: BLE001 - degraded mode
        logger.warning("model-info degraded: %s", exc)
        return {"model": "credit_scorer", "version": "fallback-rule", "status": "degraded"}


@app.post("/v1/score", response_model=DecisionResponse)
def score_application(req: ScoreRequest) -> DecisionResponse:
    features = _features_for_application(req.application_id)
    scored = _score_df(features)
    _log_decisions(scored)
    row = scored.iloc[0]
    return DecisionResponse(
        application_id=int(row["application_id"]),
        model_version=int(row["model_version"]),
        score=float(row["score"]),
        decision=row["decision"],
        threshold=float(row["threshold"]),
    )


@app.post("/v1/score-batch")
def score_batch(req: BatchScoreRequest) -> dict:
    rows = []
    for app_id in req.application_ids:
        df = _features_for_application(app_id)
        rows.append(_score_df(df))
    scored = pd.concat(rows, ignore_index=True)
    _log_decisions(scored)
    return {
        "model_version": scored["model_version"].iloc[0],
        "threshold": float(scored["threshold"].iloc[0]),
        "decisions": scored[["application_id", "score", "decision"]].to_dict(orient="records"),
    }


@app.post("/v1/score-payload")
def score_payload(req: PayloadScoreRequest) -> dict:
    """Score raw features (no DB access) — for API demos and curl examples."""
    missing = [c for c in FEATURE_COLUMNS if c not in req.features]
    if missing:
        raise HTTPException(status_code=422, detail=f"missing features: {missing}")
    df = pd.DataFrame([req.features])
    scored = _score_df(df)
    row = scored.iloc[0]
    if req.application_id:
        _log_decisions(scored.assign(application_id=req.application_id))
    return {
        "application_id": req.application_id,
        "model_version": row["model_version"],
        "score": float(row["score"]),
        "decision": row["decision"],
        "threshold": float(row["threshold"]),
    }


@app.get("/v1/decisions/recent")
def recent_decisions(limit: int = 50) -> dict:
    df = read_sql(
        "SELECT application_id, model_version, score, decision, decided_at "
        "FROM decisions ORDER BY decision_id DESC LIMIT :limit",
        {"limit": limit},
    )
    return {"count": len(df), "decisions": df.to_dict(orient="records")}


@app.post("/v1/alerts")
def ingest_alert(req: AlertPayload) -> dict:
    """Grafana webhook target: persist alert notifications (audit trail).

    Grafana notification policy routes `severity=warning` alerts here; the
    row lands in ``alert_events`` and is visible in the dashboard.
    """
    execute(
        "INSERT INTO alert_events (title, message, state, labels) "
        "VALUES (:t, :m, :s, :l)",
        {"t": req.title, "m": req.message, "s": req.state, "l": json.dumps(req.labels)},
    )
    logger.info("alert received: %s [%s]", req.title, req.state)
    return {"received": True, "title": req.title}
