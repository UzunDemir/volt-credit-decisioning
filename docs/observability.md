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

## How to use Prometheus

UI: http://localhost:9090 (Graph / Targets / Status). The `api` target must
be **UP** — it is scraped every 15s from `api:8000/metrics`.

### Metrics the API exposes

- `http_requests_total{handler, method, status}` — request counter; `status`
  is the **status class** (2xx / 4xx / 5xx), `handler` is the route
  (`/health`, `/v1/score`, `/v1/alerts`, `/model-info`, `/metrics`, …)
- `http_request_duration_seconds{handler}` — latency histogram (few buckets)
- `http_request_duration_highr_seconds` — latency histogram with many
  buckets, no handler label (more accurate quantiles)
- `http_request_size_bytes` / `http_response_size_bytes` — payload sizes
- `process_*`, `python_*` — process health (CPU, RSS, fds, GC)

### PromQL examples

```promql
# RPS (общий)
sum(rate(http_requests_total[5m]))

# RPS по эндпоинтам
sum by (handler) (rate(http_requests_total[5m]))

# RPS по классу статуса
sum by (status) (rate(http_requests_total[5m]))

# Error rate (5xx share)
sum(rate(http_requests_total{status="5xx"}[5m])) / sum(rate(http_requests_total[5m]))

# p95 latency
histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))

# p95 latency по эндпоинтам
histogram_quantile(0.95, sum by (le, handler) (rate(http_request_duration_seconds_bucket[5m])))

# QPS по скорингу
sum(rate(http_requests_total{handler="/v1/score"}[5m]))
```

To see anything move, generate traffic first: `python scripts/api_smoke.py`
or a loop over `/v1/score` (e.g. 20 requests with curl).

### Grafana panels

VoltPrometheus datasource is already provisioned (`uid: voltprom`). Use
**Explore** or add a panel with the datasource and paste any PromQL above.
The dashboard `volt_ml.json` is PostgreSQL-based (business metrics); SLO
panels over Prometheus are the documented next step.

### Alerting on SLOs (next step)

The same alert loop as the drift rule: Grafana Alerting → rule with a
PromQL condition (e.g. `error rate > 1% over 5m`), label `severity=warning`
→ the `volt-webhook` contact point → `POST /v1/alerts` → `alert_events`.

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
