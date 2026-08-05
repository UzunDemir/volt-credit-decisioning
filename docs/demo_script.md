# Demo script — 15 minutes for the interview

Goal: show the reviewer that you *built* the whole thing and understand the
why behind every part. Never read the script; use it as a spine.

## 0. Hook (30s)

"Volt is a builder shop — so I built the thing a builder would build: an
end-to-end credit decisioning platform. One command brings it up, every
piece is observable, the monitoring simulation shows what happens when the
economy turns, and the alert for drift actually calls home."

## 1. Data + SQL (3 min)

- Open `sql/01_schema.sql` + `sql/02_features.sql`.
- "Features are not pandas — they live in SQL: rolling windows over
  30/90/180 days, JSONB extraction from transaction payloads, engineered
  ratios. Training, the API and monitoring all read the *same view*, so
  train/serve skew is structurally impossible."
- Show one JSONB payload: `SELECT details FROM transactions LIMIT 1`.
- "The data is synthetic but seeded — byte-identical on any machine, and I
  simulate drift deliberately so the monitoring story is real, not staged."

## 2. Model + methodology (4 min)

- `credit_decision/model/train.py`: time-based split, why random splits are
  wrong for credit; 5-fold CV honest estimate; **calibration wrapped into
  the served model** (`CalibratedClassifierCV`, isotonic, fit on validation
  only); **threshold chosen by business cost** (`FP×1.0 + FN×0.2`), not by
  AUC.
- Open MLflow UI: runs, registered model, `production` alias, threshold tag.
- Say the numbers: "Champion is logistic — on synthetic data the features
  are almost linear, so it beats XGBoost on CV (0.697 vs 0.690). On the
  holdout test (2025 Q4, never touched during development): ROC-AUC 0.708,
  ECE 0.005 after calibration, approval rate 78% at the cost-optimal
  threshold 0.157."

## 3. Serving (2 min)

- `http://localhost:8000/docs` — OpenAPI.
- Score a real application id: `POST /v1/score` → approve/decline +
  threshold. Decision semantics: `score = P(default)`; **approve LOW risk**
  (`score <= threshold`).
- "Every call is written to `decisions` — the audit log the dashboard reads."

## 4. Monitoring + Grafana (3 min)

- `docker compose --profile full up -d` — Grafana + Airflow already running.
- Open **Grafana** (http://localhost:3000, admin/volt) → Volt ML Operations:
  - Drift share stat: latest batch **37.9%** — red (threshold 30%);
  - Bar chart: 2026-01..03 flat at 0%, April jumps to 31% and climbs —
    "that's the simulated downturn";
  - Latest decisions table: live audit trail.
- Open **Alerting → Rules**: "Drift share above 30%" — state **Firing**.
  "The rule is provisioned from the repo, not clicked together in the UI."
- Open the **Streamlit dashboard** → Monitoring tab: same story from the
  business side (drift share, score mix, top drifted features).
- "Model-performance monitoring lags 12 months because of the label delay —
  so I monitor score-distribution drift as the early-warning proxy and say
  so in the model card. That honesty is the point."

## 5. Alerting loop (1.5 min)

- `SELECT title, state, received_at FROM alert_events ORDER BY event_id DESC;`
- "The alert is not a red panel — it's a closed loop: rule → notification
  policy → webhook → our own API (`POST /v1/alerts`) → this table. Swap the
  webhook URL for Slack in one place and the same drift alert lands in the
  team channel. Provisioned from `scripts/grafana_alerts.py`, so it's
  versioned."

## 6. Orchestration (1.5 min)

- Open **Airflow** (http://localhost:8080, admin/volt) → DAGs.
- `daily_monitoring`: runs every day at 07:00 (in prod the batch would come
  from the ETL schedule; the demo window is pinned to 2026-07).
- `weekly_retrain`: retrain + registry promotion; "in prod I'd make it
  drift-triggered".
- `monthly_forecast`: portfolio default-rate forecast for finance.
- "DAGs are thin wrappers over the same `python -m credit_decision.*`
  modules — scheduling is configuration, not a second codebase."

## 7. Experiments (1.5 min)

- `python -m credit_decision.experiments.uplift`: heterogeneous effects,
  uplift@20%.
- `docs/ab_test.md`: "A blanket threshold change is a bet — I size the A/B
  test (~16k/arm, ~64 days at our volume), pre-register the analysis, and
  guard against stopping early."

## 8. Close (1 min)

- "What I'd do next at Volt: swap the generator for real data, add Azure ML
  workspace registry, make retraining drift-triggered, fairness audit on
  the score."

## Likely questions — prep

- **Why time-based split?** Credit risk is non-stationary; random CV leaks
  future into training and flatters AUC.
- **Why calibrate, and why inside the model?** Threshold is a business
  decision — uncalibrated probabilities make it wrong even with a great
  AUC. Wrapping calibration into the served model guarantees the scores the
  threshold sees are the calibrated ones.
- **Why is threshold on validation, not test?** Test must stay virgin; the
  threshold is a hyperparameter and tuning it on test overfits.
- **Why does the champion beat XGBoost?** On synthetic data features are
  nearly linear; I say this openly and note that on real data I'd expect a
  tree model to win — the pipeline compares them every retrain.
- **What about selection bias?** Labels come from the old policy; a policy
  change shifts the population — that's exactly what the drift monitor and
  the A/B design handle.
- **JSONB vs normalized tables?** JSONB for vendor-free flexible payloads;
  features are extracted at ingestion time into the view; GIN index for
  ad-hoc queries. In a real stack I'd add a nightly materialization.
- **Spark?** ETL is DataFrame-based and portable; the demo keeps the
  footprint small so it runs on one machine — the SQL is the same.
- **Why Airflow 2.10 and LocalExecutor?** One-machine demo with a real
  metastore (PostgreSQL `volt_airflow`); the DAGs are executor-agnostic, so
  moving to Celery/Kubernetes is a config change.
