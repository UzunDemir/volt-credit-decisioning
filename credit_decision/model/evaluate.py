"""Evaluation toolkit: metrics, calibration, cost-based thresholds, business impact.

The threshold is chosen NOT on statistics alone but on the economics of the
lending business: a false positive (approve a defaulter) costs ``cost_fp``,
a false negative (decline a good client) costs ``cost_fn`` — relative units.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    roc_auc_score,
    roc_curve,
)


def classification_metrics(y_true: np.ndarray, y_score: np.ndarray) -> dict[str, float]:
    """Core offline metrics for a probability score."""
    auc = roc_auc_score(y_true, y_score)
    fpr, tpr, _ = roc_curve(y_true, y_score)
    ks = float(np.max(tpr - fpr))
    ece = expected_calibration_error(y_true, y_score)
    return {
        "roc_auc": float(auc),
        "pr_auc": float(average_precision_score(y_true, y_score)),
        "gini": float(2 * auc - 1),
        "ks": ks,
        "brier": float(brier_score_loss(y_true, y_score)),
        "ece": ece,
    }


def expected_calibration_error(y_true: np.ndarray, y_score: np.ndarray, n_bins: int = 10) -> float:
    """Mean |predicted probability - observed frequency| across bins."""
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(y_score, bins) - 1, 0, n_bins - 1)
    ece = 0.0
    for b in range(n_bins):
        mask = idx == b
        if mask.sum() == 0:
            continue
        ece += mask.mean() * abs(y_score[mask].mean() - y_true[mask].mean())
    return float(ece)


def calibration_points(y_true: np.ndarray, y_score: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    """(mean predicted, observed rate) pairs for a calibration curve."""
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(y_score, bins) - 1, 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        mask = idx == b
        if mask.sum() == 0:
            continue
        rows.append({"bin": b, "mean_predicted": y_score[mask].mean(), "observed_rate": y_true[mask].mean()})
    return pd.DataFrame(rows)


def optimal_threshold(
    y_true: np.ndarray,
    y_score: np.ndarray,
    cost_fp: float = 1.0,
    cost_fn: float = 0.2,
    grid: int = 401,
) -> tuple[float, float]:
    """Threshold minimizing ``FP*cost_fp + FN*cost_fn`` on a labeled sample.

    Semantics: ``score`` is P(default); we APPROVE low-risk applicants,
    i.e. ``pred = score <= t``. A false positive approves a defaulter,
    a false negative declines a good client. With this definition the
    indifference point is ``t = cost_fn / (cost_fp + cost_fn)``.
    """
    thresholds = np.linspace(0.01, 0.99, grid)
    y_true = np.asarray(y_true, dtype=bool)
    best_t, best_cost = thresholds[0], np.inf
    for t in thresholds:
        pred = y_score <= t
        cost = float((pred & y_true).sum() * cost_fp + (~pred & ~y_true).sum() * cost_fn)
        if cost < best_cost:
            best_cost, best_t = cost, t
    return float(best_t), best_cost


def business_summary(
    y_true: np.ndarray,
    y_score: np.ndarray,
    threshold: float,
    amount: np.ndarray,
    cost_fp: float = 1.0,
    cost_fn: float = 0.2,
) -> dict[str, float]:
    """Business impact at a given threshold, in currency units.

    * expected_loss      — probability-weighted losses on approved borrowers
    * opportunity_cost   — margin foregone on declined good clients
    * total_cost         — FP + FN cost (relative units scaled by amount)
    * cost_per_applicant — total cost normalized per application
    """
    y_true = np.asarray(y_true, dtype=bool)
    amount = np.asarray(amount, dtype=float)
    pred = y_score <= threshold  # approve low-risk applicants

    approved = pred.sum()
    fp = pred & y_true
    fn = (~pred) & (~y_true)

    bad_rate = float(fp.sum() / max(approved, 1))
    expected_loss = float((amount[fp] * y_score[fp]).sum())
    opportunity_cost = float((amount[fn] * (1.0 - y_score[fn])).sum())
    total_cost = float(cost_fp * amount[fp].sum() + cost_fn * amount[fn].sum())

    return {
        "approval_rate": float(pred.mean()),
        "bad_rate_among_approved": bad_rate,
        "n_approved": int(approved),
        "n_false_positive": int(fp.sum()),
        "n_false_negative": int(fn.sum()),
        "expected_loss": expected_loss,
        "opportunity_cost": opportunity_cost,
        "total_cost": total_cost,
        "cost_per_applicant": total_cost / max(len(y_true), 1),
    }
