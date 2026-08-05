"""A/B test design utilities — sample size & runtime planning.

Standard two-proportion z-test formula (Fleiss correction):

    n per arm = (z_{1-a/2} * sqrt(2p(1-p)) + z_{1-b} * sqrt(p1(1-p1) + p2(1-p2)))^2 / (p1-p2)^2

Used by the rollout plan: before deploying a new score threshold, the
hypothesis (approval rate / bad rate unchanged, margin improves) must be
tested on a randomized slice with enough traffic — this module sizes that
experiment.
"""

from __future__ import annotations

import math


def sample_size_per_arm(
    baseline_rate: float,
    mde: float,
    alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    """Minimum sample size per arm for a two-sided binary-metric test."""
    z_alpha = 1.96 if alpha == 0.05 else _z(1 - alpha / 2)
    z_beta = _z(power)
    p1 = baseline_rate
    p2 = baseline_rate + mde
    p_bar = (p1 + p2) / 2.0
    numerator = z_alpha * math.sqrt(2 * p_bar * (1 - p_bar)) + z_beta * math.sqrt(
        p1 * (1 - p1) + p2 * (1 - p2)
    )
    return max(2, math.ceil(numerator**2 / (p1 - p2) ** 2))


def days_needed(volume_per_day: int, n_per_arm: int, holdout_share: float = 0.10) -> float:
    """Calendar days to reach ``n_per_arm`` in each arm at given traffic."""
    daily_per_arm = volume_per_day * holdout_share / 2.0
    return math.ceil(n_per_arm / max(daily_per_arm, 1.0))


def _z(p: float) -> float:
    """Inverse standard normal CDF via bisection on erf."""
    from math import erf, sqrt

    lo, hi = -6.0, 6.0
    for _ in range(64):
        mid = (lo + hi) / 2.0
        if erf(mid / sqrt(2.0)) < 2.0 * p - 1.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


if __name__ == "__main__":
    n = sample_size_per_arm(baseline_rate=0.10, mde=0.01)
    print(f"n per arm: {n:,}; days at 5k apps/day, 10% holdout: "
          f"{days_needed(5_000, n):.0f}")
