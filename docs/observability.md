# Observability with Grafana

Operational layer on top of the platform: live dashboards and **alerting**
over the data already produced by the monitoring job (`monitoring_events`)
and the API audit log (`decisions`). No new data pipelines — Grafana reads
PostgreSQL directly.

## Start

```bash
docker compose --profile full up -d
```

Grafana: http://localhost:3000  (admin / volt)

The `full` profile only adds Grafana; the base stack stays unchanged for
`docker compose up` (useful for the deployed demo link).

## What the dashboard shows

- **Drift share** (latest production batch) — stat, red when >= 30%
- **Drift detected** (latest batch) — YES/NO with color mapping
- **Drift share by production month** — bar chart; Jan-Mar flat, Apr+ red
  (the simulated downturn)
- **Applications scored per day** — timeseries from `decisions`
- **Approval rate** — windowed average over the audit log
- **Top drifted features** — per month, from the Evidently summary
- **Latest decisions** — live audit table

## Alerting

Rule **"Drift share above 30%"** — created by `scripts/grafana_alerts.py`
(compose service `grafana-alerts`, profile `full`):

```
SELECT metrics->>'share_drifted'::float
FROM monitoring_events
WHERE report_type = 'data_drift'
ORDER BY event_id DESC LIMIT 1
```

Evaluated every minute; fires when the latest batch's drifted-column share
exceeds 30%. In the demo this fires for 2026-04..07. The rule and its folder
are versioned in `scripts/grafana_alerts.py` (provisioned via the API after
Grafana starts — avoids the folder-race of file provisioning).

To add a notification channel (Slack / webhook / email):
Grafana UI → Alerting → Contact points → add contact point → Notification
policies → route `severity=warning` to it.

## Configuration layout

```
grafana/
  provisioning/
    datasources/postgres.yaml    # VoltPostgres -> volt_credit
    datasources/prometheus.yaml  # VoltPrometheus -> API SLO metrics
    dashboards/dashboards.yaml   # file provider
  dashboards/volt_ml.json        # dashboard definition
prometheus/prometheus.yml        # scrape api:8000/metrics
scripts/grafana_alerts.py        # folder + drift alert rule (API provisioning)
```

## API SLOs (Prometheus)

The API exposes request metrics (`/metrics`, prometheus-fastapi-instrumentator:
RPS, latency histogram, error rate). Prometheus scrapes them every 15s
(`prometheus/prometheus.yml`, job `api`), and the VoltPrometheus datasource
(`grafana/provisioning/datasources/prometheus.yaml`) is provisioned for
Grafana. The natural next step is SLO alert rules on error rate / latency —
routed through the same webhook loop as the drift rule.
