"""Uplift modelling — two-model approach on synthetic experiment data.

Business question: should we offer a "credit limit top-up" to clients whose
credit score is near the approval boundary? Treatment effects are
heterogeneous: the offer helps *some* segments (e.g. mid-risk) and is
wasted on others. Uplift modelling ranks clients by *incremental* effect,
not by baseline response.

Implementation notes:
  * data: synthetic, seeded; treatment assigned at random (50/50)
  * two-model: P(Y=1|X,T=1) and P(Y=1|X,T=0), uplift = difference
  * evaluation: cumulative uplift curve (AUUC-style) + uplift@decile

Usage:
    python -m credit_decision.experiments.uplift
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import KFold

from .ab_test import sample_size_per_arm


def simulate_experiment(seed: int = 7, n: int = 40_000) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    income = np.exp(rng.normal(8.0, 0.5, n))                     # log-normal income
    risk = rng.uniform(0.0, 1.0, n)                              # hidden credit risk
    treatment = rng.integers(0, 2, n).astype(bool)               # random assignment

    # baseline conversion ~35% overall: strong clients convert, weak ones don't.
    # (Kept well below the ceiling so the treatment has headroom to move it.)
    logit_base = 1.0 - 2.8 * risk + 0.35 * (np.log(income) - 8.0)
    # heterogeneous treatment effect: narrow peak at mid-risk (+~30pp),
    # zero for safe clients (no need) and hopeless ones (no chance)
    uplift_logit = 1.4 * np.exp(-((risk - 0.45) ** 2) / 0.06) * (income > 2500)
    logit = logit_base + treatment * uplift_logit
    y = rng.random(n) < 1.0 / (1.0 + np.exp(-logit))

    p0 = 1.0 / (1.0 + np.exp(-logit_base))
    p1 = 1.0 / (1.0 + np.exp(-(logit_base + uplift_logit)))

    return pd.DataFrame({
        "income": income,
        "risk": risk,
        "treatment": treatment.astype(int),
        "outcome": y.astype(int),
        "true_uplift": p1 - p0,   # known from the data-generating process
    })


def two_model_uplift(df: pd.DataFrame) -> pd.Series:
    """Uplift = P(Y|T=1) - P(Y|T=0) via two gradient-boosted models, out-of-fold.

    * GBM per arm: treatment effects are non-linear interactions, which
      linear models cannot capture (a known limitation of the two-model
      approach — in production we would use a dedicated uplift algorithm;
      the demo shows the principle and its evaluation).
    * Out-of-fold predictions per arm: avoids the classic two-model bias
      where each arm model overfits its own base rate and the difference
      becomes inflated.
    """
    X = df[["income", "risk"]]
    y_all = df["outcome"].to_numpy()
    t_all = df["treatment"].to_numpy()
    uplift = np.zeros(len(df))
    for t in (0, 1):
        idx = np.where(t_all == t)[0]
        kf = KFold(n_splits=3, shuffle=True, random_state=42 + t)
        preds = np.zeros(len(idx))
        for tr, va in kf.split(idx):
            m = GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.05)
            m.fit(X.iloc[idx[tr]], y_all[idx[tr]])
            preds[va] = m.predict_proba(X.iloc[idx[va]])[:, 1]
        uplift[idx] = preds
    return pd.Series(uplift, index=df.index)


def cumulative_gain_curve(df: pd.DataFrame, uplift: pd.Series) -> pd.DataFrame:
    """Sort by uplift, compute cumulative outcome gain vs. random targeting."""
    ordered = df.assign(uplift=uplift).sort_values("uplift", ascending=False)
    n = len(ordered)
    total = ordered["outcome"].sum()
    ordered["cum_share"] = (np.arange(n) + 1) / n
    ordered["cum_outcome"] = ordered["outcome"].cumsum() / total
    return ordered


def evaluate(df: pd.DataFrame, uplift: pd.Series) -> dict:
    curve = cumulative_gain_curve(df, uplift)
    # uplift@20%: outcome share in the top-20% targeted vs 20% baseline
    top20 = curve[curve["cum_share"] <= 0.20]
    gain = float(top20["cum_outcome"].max())
    # AUUC approx: mean cumulative gain over deciles minus random baseline
    deciles = curve.groupby(
        pd.cut(curve["cum_share"], np.linspace(0, 1, 11)), observed=True
    ).tail(1)
    auuc = float(deciles["cum_outcome"].mean() - 0.5)
    return {"uplift_at_20pct": gain, "auuc_approx": auuc, "top20_lift": gain / 0.20}


def main() -> None:
    df = simulate_experiment()
    uplift = two_model_uplift(df)
    print(f"experiment rows: {len(df):,}, conversion: {df['outcome'].mean():.1%}, "
          f"treatment: {df['treatment'].mean():.1%}")
    print("  naive AUC (outcome vs risk): "
          f"{roc_auc_score(df['outcome'], df['risk']):.3f}")
    print(f"  {evaluate(df, uplift)}")
    # A/B design for the same experiment
    n_arm = sample_size_per_arm(
        baseline_rate=df["outcome"].mean(),
        mde=0.01, alpha=0.05, power=0.8,
    )
    print(f"  sample size per arm (MDE=1pp): {n_arm:,}")


if __name__ == "__main__":
    main()
