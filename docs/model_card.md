# Model Card — credit_scorer

## Overview

- **Model**: `credit_scorer` (champion: XGBoost), registered in MLflow, `production` alias.
- **Task**: binary classification — probability that a borrower defaults on
  any obligation within 12 months of application.
- **Decision**: `approve` if `P(default) < threshold`, where the threshold
  minimizes `FP * cost_fp + FN * cost_fn` on the validation window.
- **Intended use**: first-line automated credit decisioning for a digital
  lender; humans review borderline cases (see *Human-in-the-loop*).

## Data

- **Source**: seeded synthetic generator (`credit_decision/etl/generate.py`),
  distribution-shaped after public credit-risk data (default rate ~8–12%).
- **Window**: applications 2023-01 .. 2025-12 (label available); production
  months 2026-01 .. (unlabeled, scoring only).
- **Granularity**: application-level; per-client transaction history
  (structured + JSONB `details`) up to 180 days before application.
- **Label**: `has_default_12m` — default on any obligation within 12 months.
  Deliberately *not* the 30/60/90-day delinquency flags: 12 months matches
  the loan book horizon.

## Features (all from `v_credit_features`)

- **Static**: income, age, employment status, purpose, amount, term,
  credit history length, open loans, tenure, region-independent flags.
- **Transaction (SQL window aggregates)**: outflow/inflow counts & sums over
  30/90/180d, category & merchant diversity, night spend, mobile share,
  geo-city diversity — plus engineered ratios (utilization, spend trend,
  inflow coverage).
- **Missingness**: income missing flag (7% of applicants).

## Methodology

1. Time-based split: train < 2025-07, val 2025-07..09, test 2025-10..12.
2. Candidates: logistic regression, random forest, XGBoost; chosen by mean
   5-fold stratified CV AUC on train.
3. Isotonic calibration fitted on **validation** predictions only, applied to
   test — calibration metrics cannot be optimistic.
4. Cost-optimal threshold on validation: `FP*cost_fp + FN*cost_fn`
   (defaults `cost_fp=1.0`, `cost_fn=0.2`); threshold stored as a registry
   tag, not hard-coded.
5. All runs tracked in MLflow (`credit_scoring` experiment).

## Evaluation (holdout test, calibrated)

Reported live in MLflow run metrics and `/model-info`:

- ROC-AUC, PR-AUC, Gini, KS, Brier, ECE (expected calibration error)
- Business: approval rate, bad rate among approved, expected loss,
  cost per applicant

## Known limitations & risks

- **Synthetic data**: fine for demonstrating the platform; real data requires
  re-training and re-validation before any production use.
- **Label delay**: 12-month outcome → model-performance monitoring lags;
  score-distribution drift is the early-warning proxy.
- **Selection bias**: labels come from *historical decision policy*. If the
  policy changes (new threshold), the scoring population shifts; the
  monitoring job is designed to surface this (see `docs/ab_test.md`).
- **Fairness**: no protected-attribute audit yet; age/region are available
  and should be tested for disparate impact before real deployment.
- **No causal claims**: the score is predictive, not causal.

## Human-in-the-loop

Applications with scores in a band around the threshold (e.g.
`threshold ± 0.05`) should route to manual review — the exact band is a
business decision; the API supports returning the score and threshold for
any review workflow.

## Monitoring (see `monitoring/`)

- Per production month: Evidently **data drift** (feature + score
  distribution vs 2025-H2 reference) and **data quality** reports.
- Summaries in `monitoring_events`; HTML reports in `mlartifacts/monitoring/`.
- Alerting rule (dashboard): `share_of_drifted_columns > 0.3` → investigate.
