# Architecture

```
                        ┌──────────────────────────────────────────────┐
                        │                   PostgreSQL                  │
                        │  clients │ applications │ transactions(JSONB)│
                        │  decisions │ monitoring_events │ alert_events│
                        └───────▲──────────────▲───────────────▲───────┘
                                │              │               │
              sql/01_schema.sql │              │               │ SQL COPY
              sql/02_features.sql (v_credit_features view)
                                │              │               │
   ┌──────────────┐   ┌─────────┴──────┐   ┌───┴─────────┐   ┌─┴─────────────────┐
   │   etl job    │   │   train job    │   │    api      │   │  monitor job      │
   │ seeded data  │──▶│ baseline vs    │──▶│ FastAPI     │◀──│ Evidently drift   │
   │ generator +  │   │ XGBoost, calib,│   │ /v1/score   │   │ + data quality    │
   │ COPY loader  │   │ cost threshold │   │ /v1/alerts  │   │ per prod month    │
   └──────────────┘   └───────┬────────┘   └──────┬──────┘   └──────────────────┘
                              │ MLflow            │  ▲ webhook
                       ┌──────▼──────┐     ┌──────┴──┴────────┐
                       │   MLflow    │     │     Grafana      │── alert rule
                       │ tracking +  │     │ dashboards +     │   drift ≥ 30%
                       │ registry    │     │ alerting         │
                       └─────────────┘     └──────────────────┘
   ┌─────────────────────────────────────────────┐
   │  Airflow (profile full)                     │
   │  daily_monitoring · weekly_retrain ·        │   Streamlit dashboard
   │  monthly_forecast  (BashOperator over      │── business views
   │  python -m credit_decision.*)               │
   └─────────────────────────────────────────────┘
```

## Components

| Service | Image | Purpose |
|---|---|---|
| `postgres` | postgres:16-alpine | all data; schema auto-applied from `sql/` |
| `etl` | this repo | generate seeded data (structured + JSONB), load via COPY |
| `train` | this repo | CV, calibration, cost threshold; registers `credit_scorer` |
| `mlflow` | this repo | tracking + registry (`production` alias); server-local artifacts in shared `mlartifacts` volume |
| `api` | this repo | uvicorn FastAPI; features from SQL view -> model -> decision; alert webhook target (`/v1/alerts`) |
| `dashboard` | this repo | Streamlit business dashboard |
| `monitor` | this repo | simulate prod batches; Evidently drift/quality reports |
| `grafana` | grafana/grafana-oss:13.0.2 | operational dashboards + alert rule (profile `full`) |
| `grafana-alerts` | this repo | one-shot provisioning: folder + rule + contact point + policy |
| `airflow-init` | volt-airflow | one-shot: create `volt_airflow` DB, migrate, admin user |
| `airflow-scheduler` | volt-airflow | LocalExecutor; runs DAG tasks (mounts `mlartifacts`) |
| `airflow-webserver` | volt-airflow | Airflow UI + REST API |

## Data flow

1. **ETL** generates a training window (2023-01 .. 2025-12, labeled) and
   loads `clients`, `applications`, `transactions` (JSONB `details`).
2. **Features live in SQL**: `v_credit_features` computes rolling windows
   (30/90/180d), JSONB extraction, engineered ratios. Training, serving and
   monitoring read the *same view* — no train/serve skew by construction.
3. **Training** runs a time-based split (train < 2025-07, val 2025-07..09,
   test 2025-10..12), compares logistic / random forest / XGBoost by 5-fold
   CV AUC, wraps the champion in `CalibratedClassifierCV` (isotonic, fit on
   validation only — calibration is part of the *served* model), picks the
   approval threshold to minimize `FP*cost_fp + FN*cost_fn` on val, then
   registers the champion and promotes it via the `production` alias.
4. **API** loads the Production model + threshold from the registry. Every
   `/v1/score` call computes features for the application id from SQL,
   scores (calibrated probability of default), approves when
   `score <= threshold`, and logs the decision into `decisions`.
5. **Monitoring** scores each simulated production month, runs Evidently
   DataDrift + DataQuality reports against the 2025-H2 reference window, and
   writes summaries into `monitoring_events`. The simulation injects a
   downturn from 2026-04 so the dashboard shows alerts firing.
6. **Grafana** reads PostgreSQL directly: drift share by month, approval
   rate, audit table. The alert rule queries the latest event; when
   `share_drifted >= 0.3` it fires and the notification policy routes
   `severity=warning` to the `volt-webhook` contact point → `POST /v1/alerts`
   on the API → row in `alert_events` (closed loop, versioned in the repo).
7. **Airflow** schedules the jobs: `daily_monitoring` (Evidently on the
   latest batch), `weekly_retrain` (train + registry promotion),
   `monthly_forecast` (Holt-Winters portfolio forecast). DAGs are thin
   `BashOperator` wrappers over `python -m credit_decision.*` — scheduling
   is configuration, no duplicated ML logic.
8. **Dashboard** reads `decisions` + `monitoring_events` + `/model-info`:
   approval rate, score mix, drift by month, live scoring demo.

## Design decisions

- **Synthetic but realistic data, seeded.** Reproducible on any machine;
  the monitoring story simulates drift deliberately. The SQL layer is
  identical to what real data would use.
- **No train/serve skew.** One feature view, one feature contract
  (`model/pipeline.py`), consumed everywhere.
- **Calibration is part of the served model.** `CalibratedClassifierCV`
  wraps the fitted pipeline at training time; production scores are
  calibrated, so the cost-based threshold stays valid. (Not a detached
  post-hoc step that serving could forget.)
- **Threshold is a business decision.** Stored with the model in the
  registry as a tag — changing the FP/FN cost ratio only requires
  retraining.
- **Alerting is a closed loop.** Rule → policy → webhook → API → DB table,
  all provisioned from the repo (`scripts/grafana_alerts.py`,
  `grafana/provisioning/`), replaceable by Slack/Teams in one place.
- **Model performance monitoring needs 12-month-delayed labels.** The demo
  uses score-distribution drift as the early-warning proxy (documented in
  `model_card.md`).
- **Airflow as scheduler only.** Jobs are plain Python modules; DAGs just
  invoke them on schedule. The metastore lives in a dedicated PostgreSQL
  database (`volt_airflow`) created by a one-shot init service.
