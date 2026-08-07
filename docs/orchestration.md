# Orchestration with Airflow

Scheduling layer for the platform, started with the `full` profile:

```bash
docker compose --profile full up -d
```

Airflow UI: http://localhost:8080  (admin / volt)

## DAGs

| DAG | Schedule | Command | Purpose |
|-----|----------|---------|---------|
| `daily_monitoring` | daily 07:00 | `python -m credit_decision.monitoring.run_monitor --month 2026-07` | Evidently drift + quality report on the latest production batch |
| `weekly_retrain` | Monday 03:00 | `python -m credit_decision.model.train` | Retrain champion, register to MLflow, update `production` alias |
| `drift_retrain` | daily 07:30 | check drift >= 30% -> `python -m credit_decision.model.train --candidate` | Drift-triggered retraining: registers a CANDIDATE, production alias untouched (champion-challenger) |
| `monthly_forecast` | 1st of month 04:00 | `python -m credit_decision.model.forecast` | Portfolio default-rate forecast (Holt-Winters), artifacts to MLflow |

DAG code lives in `dags/` and invokes the same modules the one-shot compose
services use - `python -m credit_decision.*` with the project on `PYTHONPATH`.
Scheduling *is* configuration: no new ML logic inside Airflow.

`drift_retrain` demonstrates the champion-challenger loop: the DAG probes
the latest drift share and, when the threshold fires, trains a candidate
(`--candidate` - no alias promotion). The API then shadow-scores it
(`decisions.challenger_score`) until labels arrive for offline comparison.

In production the monitoring batch would be derived from the ETL schedule
(previous calendar month); the demo window is pinned to `2026-07` with a
comment in the DAG.

## Architecture

- Official `apache/airflow:2.10.5-python3.11` image (`Dockerfile.airflow`)
  plus the project requirements - DAGs run the real stack (SQLAlchemy,
  MLflow, Evidently), not a stubbed environment.
- LocalExecutor; metastore in a dedicated PostgreSQL database
  (`volt_airflow`) created by the one-shot `airflow-init` service
  (`scripts/airflow_init.py`).
- DB/MLflow credentials come from the compose environment, shared with the
  rest of the stack (`*db_env`).

## Demo flow

1. Open http://localhost:8080 -> DAGs -> `daily_monitoring`.
2. Trigger it manually (play button) or wait for the schedule.
3. Watch the run: task `run_monitor` executes the Evidently pipeline;
   the drift/quality events land in `monitoring_events` (Grafana panels
   update within the 30s refresh) and HTML reports in `mlartifacts/monitoring/`.

## Notes

- **Permissions**: Airflow tasks run as uid 50000 and MLflow writes a
  `registered_model_meta` file next to the loaded model - the shared
  `mlartifacts` volume must be writable by non-root. The image ships
  `/mlartifacts` with mode 1777, so fresh volumes inherit it; if the volume
  was created earlier (root-owned), fix once with:
  `docker compose exec -T api chmod -R a+rwX /mlartifacts`
- The scheduler also mounts `mlartifacts` (LocalExecutor runs tasks inside
  the scheduler container).
- Retries: `daily_monitoring` has one retry; failed runs are visible in the
  UI Grid view with the full BashOperator output.

## Alerting loop (closed demo)

Grafana rule "Drift share above 30%" fires on the latest batch -> notification
policy routes `severity=warning` to contact point `volt-webhook`
(http://api:8000/v1/alerts) -> FastAPI persists the event into
`alert_events` (visible in the dashboard / queryable in PostgreSQL).

Replace the webhook URL with a Slack/Teams endpoint for real delivery.
