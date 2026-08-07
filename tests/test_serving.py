"""Unit tests for the serving layer — no database required."""

from __future__ import annotations

from credit_decision.model.pipeline import FEATURE_COLUMNS
from credit_decision.serving import api


def _feature_row() -> dict:
    row = {c: 0.0 for c in FEATURE_COLUMNS}
    row["employment_status"] = "employed"
    row["purpose"] = "personal"
    return row


def test_score_payload_degraded_mode_returns_fallback(monkeypatch):
    """MLflow unreachable -> rule-based fallback, not a 500.

    Regression: model_version='fallback-rule' used to crash int(...) in the
    response path, and _score_df required an application_id column that
    score-payload does not provide.
    """

    def _boom(*args, **kwargs):
        raise RuntimeError("mlflow down")

    monkeypatch.setattr(api, "get_production_model", _boom)
    out = api.score_payload(api.PayloadScoreRequest(features=_feature_row()))
    assert out["model_version"] == "fallback-rule"
    assert out["decision"] in ("approve", "decline")
    assert 0.0 <= out["score"] <= 1.0


def test_score_payload_degraded_mode_skips_candidate(monkeypatch):
    """The candidate must not be loaded when the champion is unavailable."""

    def _boom(*args, **kwargs):
        raise RuntimeError("mlflow down")

    monkeypatch.setattr(api, "get_production_model", _boom)

    def _candidate_should_not_load(*args, **kwargs):
        raise AssertionError("candidate must not load in degraded mode")

    monkeypatch.setattr(api, "get_candidate_model", _candidate_should_not_load)
    out = api.score_payload(api.PayloadScoreRequest(features=_feature_row()))
    assert out["decision"] in ("approve", "decline")
