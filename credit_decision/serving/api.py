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
from contextlib import asynccontextmanager

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..db import execute, read_sql
from ..model.pipeline import FEATURE_COLUMNS
from .model_loader import get_production_model

logger = logging.getLogger("credit_decision.serving")


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_production_model()  # warm-up: fail fast if no production model
    yield


app = FastAPI(
    title="Volt Credit Decisioning API",
    description="Credit scoring: features from SQL view -> champion model -> business decision.",
    version="0.1.0",
    lifespan=lifespan,
)


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
    model_version: int
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


def _score_df(features: pd.DataFrame) -> pd.DataFrame:
    """Attach score + decision to a feature frame."""
    prod = get_production_model()
    X = features[FEATURE_COLUMNS]
    score = prod["model"].predict_proba(X)[:, 1]
    out = features[["application_id"]].copy()
    out["score"] = score
    # approve LOW-risk applicants: score = P(default) <= threshold
    out["decision"] = np.where(score <= prod["threshold"], "approve", "decline")
    out["threshold"] = prod["threshold"]
    out["model_version"] = prod["version"]
    return out


def _log_decisions(scored: pd.DataFrame) -> None:
    for _, row in scored.iterrows():
        execute(
            "INSERT INTO decisions (application_id, model_version, score, decision) "
            "VALUES (:app, :ver, :score, :decision)",
            {
                "app": int(row["application_id"]),
                "ver": int(row["model_version"]),
                "score": float(row["score"]),
                "decision": row["decision"],
            },
        )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/model-info")
def model_info() -> dict:
    prod = get_production_model()
    return {
        "model": "credit_scorer",
        "version": prod["version"],
        "champion": prod["champion"],
        "threshold": prod["threshold"],
        "test_roc_auc": prod["test_roc_auc"],
        "run_id": prod["run_id"],
    }


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
        "model_version": int(scored["model_version"].iloc[0]),
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
        "model_version": int(row["model_version"]),
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
        "CREATE TABLE IF NOT EXISTS alert_events ("
        " event_id BIGSERIAL PRIMARY KEY,"
        " title TEXT NOT NULL,"
        " message TEXT,"
        " state TEXT,"
        " labels JSONB,"
        " received_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    execute(
        "INSERT INTO alert_events (title, message, state, labels) "
        "VALUES (:t, :m, :s, :l)",
        {"t": req.title, "m": req.message, "s": req.state, "l": json.dumps(req.labels)},
    )
    logger.info("alert received: %s [%s]", req.title, req.state)
    return {"received": True, "title": req.title}
