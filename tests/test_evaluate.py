"""Unit tests for the evaluation toolkit."""

from __future__ import annotations

import numpy as np
import pytest

from credit_decision.model import evaluate


def test_optimal_threshold_perfect_separation():
    # approve LOW-risk (score <= t); with perfect separation and FN cheaper
    # than FP, the optimum sits just below the class boundary
    rng = np.random.default_rng(0)
    score = rng.uniform(0, 1, 2_000)
    y = (score > 0.5).astype(int)
    t, cost = evaluate.optimal_threshold(y, score, cost_fp=1.0, cost_fn=0.2)
    # the class boundary itself is in the grid and yields zero cost
    assert 0.45 < t < 0.55
    assert cost == 0.0


def test_optimal_threshold_respects_cost_ratio():
    # more expensive FPs -> approve less -> LOWER threshold;
    # noisy labels keep separation imperfect so cost trade-offs matter
    rng = np.random.default_rng(1)
    score = rng.uniform(0, 1, 5_000)
    y = (score + rng.normal(0, 0.15, 5_000) > 0.5).astype(int)
    t_fp_expensive, _ = evaluate.optimal_threshold(y, score, cost_fp=5.0, cost_fn=0.1)
    t_fn_expensive, _ = evaluate.optimal_threshold(y, score, cost_fp=0.1, cost_fn=5.0)
    assert t_fp_expensive < t_fn_expensive


def test_ece_perfectly_miscalibrated():
    y = np.array([0, 0, 0, 1, 1, 1])
    score = np.array([1, 1, 1, 0, 0, 0])
    assert evaluate.expected_calibration_error(y, score) == pytest.approx(1.0)


def test_metrics_match_sklearn():
    rng = np.random.default_rng(2)
    y = rng.integers(0, 2, 500)
    score = rng.uniform(0, 1, 500)
    m = evaluate.classification_metrics(y, score)
    from sklearn.metrics import roc_auc_score

    assert m["roc_auc"] == pytest.approx(roc_auc_score(y, score))
    assert m["gini"] == pytest.approx(2 * m["roc_auc"] - 1)


def test_business_summary_hand_computed():
    # approve low-risk (score <= 0.5): only row 2 qualifies (good, score 0.3)
    y = np.array([0, 1, 0, 1])
    score = np.array([0.8, 0.7, 0.3, 0.9])
    amount = np.array([1000.0, 2000.0, 1500.0, 500.0])
    biz = evaluate.business_summary(y, score, threshold=0.5, amount=amount, cost_fp=1.0, cost_fn=0.2)
    assert biz["n_approved"] == 1
    assert biz["n_false_positive"] == 0
    assert biz["n_false_negative"] == 1  # row 0: good client declined
    assert biz["approval_rate"] == pytest.approx(0.25)
    assert biz["bad_rate_among_approved"] == pytest.approx(0.0)
    assert biz["total_cost"] == pytest.approx(0.2 * 1000)
