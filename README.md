# Volt Credit Decisioning — end-to-end ML platform

<p align="left"><img src="credit_decision/dashboard/assets/volt_logo.png" width="96" alt="Volt logo"></p>

A production-shaped credit scoring platform for a digital lender: **SQL
feature engineering → MLflow-tracked training → FastAPI serving → Evidently
drift monitoring → Streamlit business dashboard → Grafana alerting → Airflow
orchestration**, all in one `docker compose` stack.

Built as a portfolio project for a **Senior Data Scientist** role in
fintech: every layer exists to be inspected and questioned in an interview.

## Quickstart

```bash
docker compose up --build
```

`etl` and `train` run once and exit (seed data → train → register model);
`api` and `dashboard` stay up. Then:

| What | Where |
|---|---|
| API (OpenAPI docs) | http://localhost:8000/docs |
| Business dashboard | http://localhost:8501 |
| MLflow (runs + registry) | http://localhost:5000 |
| PostgreSQL | localhost:5433 (volt/volt/volt_credit) |

Full observability + orchestration profile:

```bash
docker compose --profile full up -d
```

| What | Where | Credentials |
|---|---|---|
| Grafana (drift/approval dashboards + alerts) | http://localhost:3000 | admin / volt |
| Airflow (4 DAGs: `daily_monitoring`, `drift_retrain`, `weekly_retrain`, `monthly_forecast`) | http://localhost:8080 | admin / volt |

Simulate production months and generate drift reports:

```bash
docker compose run --no-deps --rm monitor python -m credit_decision.monitoring.run_monitor --simulate
```

Then reload the dashboard → **Monitoring** tab: months 2026-01..03 are
steady, 2026-04+ shift into a downturn — drift alerts fire.

Try the API:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/model-info
curl -X POST http://localhost:8000/v1/score -H "Content-Type: application/json" \
     -d '{"application_id": 123456}'
```

## What the platform demonstrates

- **Production SQL**: features live in `v_credit_features`
  (`sql/02_features.sql`) — rolling windows (30/90/180d), JSONB extraction
  from semi-structured transaction payloads, engineered ratios. One view
  consumed by training, serving and monitoring → no train/serve skew.
- **Methodology**: time-based split (train < 2025-07, val 2025-07..09,
  test 2025-10..12), honest 5-fold CV, **isotonic calibration wrapped into
  the served model** (fit on validation only, so production scores are
  calibrated), approval threshold chosen by **business cost**
  (`FP×cost_fp + FN×cost_fn`), not by AUC.
- **MLOps**: MLflow tracking + registry (`production` alias, threshold as a
  model tag), Docker Compose, CI (ruff + pytest + docker build), unit +
  DB-integration tests, model card, A/B and deployment docs.
- **Observability**: Evidently data-drift + data-quality reports per
  production month (deliberate downturn from 2026-04), Grafana dashboards
  over PostgreSQL, **alert rule → webhook → API → `alert_events`** closed
  loop (drift ≥ 30% fires a real notification).
- **Orchestration**: Airflow 2.10 (LocalExecutor, dedicated `volt_airflow`
  metastore) schedules monitoring, retraining (drift-triggered candidate via
  `drift_retrain`) and forecasting; DAGs call the same
  `python -m credit_decision.*` modules the one-shot jobs use.
- **Experimentation**: A/B test sizing (`experiments/ab_test.py`), uplift
  modelling (`experiments/uplift.py`), rollout plan (`docs/ab_test.md`).
- **Forecasting**: Holt-Winters portfolio default-rate forecast
  (`model/forecast.py`), backtest MAPE logged to MLflow.

## Architecture at a glance

```
sql/01_schema.sql + 02_features.sql (v_credit_features)
        │
   ┌────▼────┐   ┌───────▼──────┐   ┌────────▼───────┐
   │   etl   │──▶│    train     │──▶│      api       │
   │ seeded  │   │ CV + calib + │   │ FastAPI /v1/*  │
   │  data   │   │ cost thresh  │   │ decisions log  │
   └─────────┘   └──────┬───────┘   └───┬─────┬──────┘
                        │ MLflow       │     │
                 ┌──────▼──────┐  ┌────▼─────▼─────┐
                 │    MLflow   │  │   Streamlit    │
                 │ registry    │  │   dashboard    │
                 │ production  │  └───────┬────────┘
                 │    alias    │          │
                 └─────────────┘   ┌──────▼────────┐
                                   │    Grafana    │──alert ≥30%──▶ POST /v1/alerts
                                   │ panels+rule  │                → alert_events (DB)
                                   └───────────────┘
   Airflow (profile full): daily_monitoring · weekly_retrain · monthly_forecast
   Evidently monitor job: reference 2025-H2 vs monthly batches → monitoring_events
```

## Repository layout

```
sql/                  schema + feature view (the source of truth)
credit_decision/
  etl/                seeded generator + COPY loader + production simulation
  model/              pipeline contract, training, evaluation, forecasting
  serving/            FastAPI + MLflow registry loader + alert webhook target
  monitoring/         Evidently drift/quality job
  experiments/        A/B sizing + uplift
  dashboard/          Streamlit business dashboard (+ assets/volt_logo.png)
dags/                 Airflow DAGs (monitoring / retrain / forecast)
grafana/              provisioned dashboards + datasource
tests/                unit tests (no DB) + integration tests (skipped w/o DB)
docs/                 architecture, model card, A/B plan, deployment,
                      observability, orchestration, demo script
scripts/              smoke checks + provisioning helpers
```

## Verification

```bash
pip install -r requirements.txt
pytest tests -q                      # unit tests (no database needed)
python scripts/smoke_generate.py     # generator sanity + drift simulation check
python -m credit_decision.experiments.uplift
python -m credit_decision.model.forecast   # needs DB (or run inside compose)
```

## Deployment

Public-link deployment is covered in `docs/deployment.md`: **Render** (free,
no credit card) and **Azure App Service** (PostgreSQL Flexible Server +
optional Azure ML workspace registry — the Azure story from the vacancy).
The `full` profile (Grafana/Airflow) is designed for the local demo; the
deployed link runs the base stack.

## Why synthetic data?

The demo must be reproducible on any machine and must *show* monitoring
working — so the generator is seeded (byte-identical on every run) and the
production simulation injects a controllable downturn. The SQL, pipelines
and serving layers are identical to what real data would use; swapping the
generator for a real ingestion is the documented first step
(`docs/model_card.md` → *Known limitations*).

## Docs

- [Architecture](docs/architecture.md)
- [Model card](docs/model_card.md)
- [A/B test design](docs/ab_test.md)
- [Deployment](docs/deployment.md)
- [Observability (Grafana)](docs/observability.md)
- [Orchestration (Airflow)](docs/orchestration.md)
- [Interview demo script](docs/demo_script.md)
