"""Unit tests for A/B test sizing and uplift modelling."""

from __future__ import annotations

from credit_decision.experiments.ab_test import days_needed, sample_size_per_arm
from credit_decision.experiments.uplift import evaluate, simulate_experiment, two_model_uplift


def test_sample_size_reference_value():
    # baseline 10%, MDE 1pp, alpha 0.05, power 0.8 -> ~16k per arm (Fleiss)
    n = sample_size_per_arm(baseline_rate=0.10, mde=0.01)
    assert 10_000 < n < 30_000


def test_sample_size_monotonic_in_mde():
    small_mde = sample_size_per_arm(0.10, 0.005)
    large_mde = sample_size_per_arm(0.10, 0.02)
    assert small_mde > large_mde


def test_days_needed_scales_with_traffic():
    n = sample_size_per_arm(0.10, 0.01)
    assert days_needed(1_000, n) > days_needed(10_000, n)


def test_uplift_finds_heterogeneous_effect():
    df = simulate_experiment(seed=7, n=20_000)
    uplift = two_model_uplift(df)
    res = evaluate(df, uplift)
    # top 20% by uplift should contain far more than 20% of outcomes
    assert res["uplift_at_20pct"] > 0.30
    assert res["auuc_approx"] > 0.05


def test_uplift_ranking_correlates_with_true_effect():
    # the two-model estimate must rank clients like the true heterogeneous
    # effect from the data-generating process (Spearman on 20k rows)
    from scipy.stats import spearmanr

    df = simulate_experiment(seed=7, n=20_000)
    uplift = two_model_uplift(df)
    rho = spearmanr(uplift, df["true_uplift"]).statistic
    assert rho > 0.4
