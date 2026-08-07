"""Provision Grafana alerting via API: folder, rules, contact point, policy.

Runs after Grafana starts; idempotent. Reads credentials from env.

Rules provisioned:
  * "Drift share above 30%" — PostgreSQL (voltdb), business drift alert
  * "Error rate above 1% (5xx)" — Prometheus (voltprom), API SLO
  * "API p95 latency above 300ms" — Prometheus (voltprom), API SLO

The webhook target is our own FastAPI (/v1/alerts) which persists every
notification into ``alert_events`` — a closed-loop demo of ML alerting:
rule -> notification policy -> webhook -> API -> audit table.

Usage:
    python scripts/grafana_alerts.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

BASE = os.environ.get("GRAFANA_URL", "http://localhost:3000").rstrip("/")
AUTH = (
    os.environ.get("GRAFANA_ADMIN_USER", "admin"),
    os.environ.get("GRAFANA_ADMIN_PASSWORD", "volt"),
)
WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL", "http://api:8000/v1/alerts")
FOLDER_UID = "volt-folder"
FOLDER_TITLE = "Volt"
CONTACT_NAME = "volt-webhook"

RULE = {
    "title": "Drift share above 30%",
    "folderUID": FOLDER_UID,
    "ruleGroup": "volt-drift",
    "condition": "C",
    "for": "1m",
    "noDataState": "NoData",
    "execErrState": "Error",
    "isPaused": False,
    "labels": {"severity": "warning"},
    "annotations": {"summary": "Drift share {{ $values.B }} on the latest production batch"},
    "data": [
        {
            "refId": "A",
            "relativeTimeRange": {"from": 300, "to": 0},
            "datasourceUid": "voltdb",
            "queryType": "table",
            "model": {
                "refId": "A",
                "format": "table",
                "rawQuery": True,
                "rawSql": "SELECT (metrics->>'share_drifted')::float AS share FROM monitoring_events "
                          "WHERE report_type = 'data_drift' ORDER BY event_id DESC LIMIT 1",
                "datasource": {"type": "postgres", "uid": "voltdb"},
                "intervalMs": 1000,
                "maxDataPoints": 100,
            },
        },
        {"refId": "B", "datasourceUid": "__expr__", "queryType": "",
         "model": {"type": "reduce", "expression": "A", "reducer": "last",
                   "intervalMs": 1000, "maxDataPoints": 43200}},
        {"refId": "C", "datasourceUid": "__expr__", "queryType": "",
         "model": {"type": "threshold", "expression": "B",
                   "conditions": [{"evaluator": {"type": "gt", "params": [0.3]},
                                   "operator": {"type": "and"},
                                   "query": {"params": ["C"]}}],
                   "intervalMs": 1000, "maxDataPoints": 43200}},
    ],
}



def _promql_rule(title: str, expr: str, evaluator: dict, for_: str) -> dict:
    """Build a Grafana alert rule over the VoltPrometheus datasource.

    Shape mirrors RULE: query (A) -> reduce (B) -> threshold (C). Labels
    carry ``severity=warning`` so the notification policy routes firings to
    the volt-webhook contact point.
    """
    return {
        "title": title,
        "folderUID": FOLDER_UID,
        "ruleGroup": "volt-slo",
        "condition": "C",
        "for": for_,
        "noDataState": "NoData",
        "execErrState": "Error",
        "isPaused": False,
        "labels": {"severity": "warning"},
        "annotations": {"summary": title},
        "data": [
            {
                "refId": "A",
                "relativeTimeRange": {"from": 300, "to": 0},
                "datasourceUid": "voltprom",
                "queryType": "timeSeriesQuery",
                "model": {
                    "refId": "A",
                    "expr": expr,
                    "datasource": {"type": "prometheus", "uid": "voltprom"},
                    "queryType": "timeSeriesQuery",
                    "instant": True,
                    "range": False,
                    "intervalMs": 1000,
                    "maxDataPoints": 43200,
                },
            },
            {"refId": "B", "datasourceUid": "__expr__", "queryType": "",
             "model": {"type": "reduce", "expression": "A", "reducer": "last",
                       "intervalMs": 1000, "maxDataPoints": 43200}},
            {"refId": "C", "datasourceUid": "__expr__", "queryType": "",
             "model": {"type": "threshold", "expression": "B",
                       "conditions": [{"evaluator": evaluator,
                                       "operator": {"type": "and"},
                                       "query": {"params": ["C"]}}],
                       "intervalMs": 1000, "maxDataPoints": 43200}},
        ],
    }


SLO_RULES = [
    _promql_rule(
        "Error rate above 1% (5xx)",
        '(sum(rate(http_requests_total{status="5xx"}[5m])) or vector(0)) / sum(rate(http_requests_total[5m]))',
        {"type": "gt", "params": [0.01]},
        "5m",
    ),
    _promql_rule(
        "API p95 latency above 300ms",
        "histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))",
        {"type": "gt", "params": [0.3]},
        "5m",
    ),
]

def wait_ready(timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE}/api/health", auth=AUTH, timeout=3)
            if r.status_code == 200 and r.json().get("database") == "ok":
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
    raise SystemExit("Grafana did not become ready in time")


def ensure_folder() -> str:
    r = httpx.get(f"{BASE}/api/folders/{FOLDER_UID}", auth=AUTH, timeout=5)
    if r.status_code == 200:
        return FOLDER_UID
    r = httpx.post(f"{BASE}/api/folders", auth=AUTH, timeout=5,
                   json={"uid": FOLDER_UID, "title": FOLDER_TITLE})
    if r.status_code not in (200, 201, 409):
        raise SystemExit(f"folder creation failed: {r.status_code} {r.text}")
    return FOLDER_UID


def ensure_rule(rule: dict | None = None) -> None:
    """Create the rule if it does not exist yet (idempotent by title)."""
    rule = rule or RULE
    rules = httpx.get(f"{BASE}/api/v1/provisioning/alert-rules", auth=AUTH, timeout=5).json()
    if any(r.get("title") == rule["title"] for r in rules):
        print("alert rule already exists:", rule["title"])
        return
    r = httpx.post(f"{BASE}/api/v1/provisioning/alert-rules", auth=AUTH, timeout=10, json=rule)
    if r.status_code not in (200, 201):
        raise SystemExit(f"alert rule creation failed: {r.status_code} {r.text}")
    print("alert rule created:", r.json().get("uid"), "-", rule["title"])


def ensure_contact_point() -> None:
    cps = httpx.get(f"{BASE}/api/v1/provisioning/contact-points", auth=AUTH, timeout=5).json()
    if any(cp.get("name") == CONTACT_NAME for cp in cps):
        print("contact point already exists")
        return
    payload = {
        "name": CONTACT_NAME,
        "type": "webhook",
        "settings": {"url": WEBHOOK_URL, "httpMethod": "POST"},
    }
    r = httpx.post(f"{BASE}/api/v1/provisioning/contact-points", auth=AUTH, timeout=10, json=payload)
    if r.status_code not in (200, 201, 202):
        raise SystemExit(f"contact point creation failed: {r.status_code} {r.text}")
    print(f"contact point created -> {WEBHOOK_URL}")


def ensure_policy() -> None:
    """Route severity=warning alerts to the volt webhook (default route stays).

    Grafana 13 rejects the string-matchers form here ("bad request data");
    the object_matchers shape is the one that validates.
    """
    r = httpx.get(f"{BASE}/api/v1/provisioning/policies", auth=AUTH, timeout=5)
    if r.status_code != 200:
        raise SystemExit(f"policy fetch failed: {r.status_code} {r.text}")
    tree = r.json()
    routes = tree.get("routes") or []
    if any(rt.get("receiver") == CONTACT_NAME for rt in routes):
        print("policy route already exists")
        return
    tree["receiver"] = CONTACT_NAME
    tree["group_wait"] = "30s"
    tree["group_interval"] = "5m"
    tree["repeat_interval"] = "4h"
    tree["routes"] = routes + [{
        "receiver": CONTACT_NAME,
        "object_matchers": [["severity", "=", "warning"]],
        "group_by": ["alertname"],
        "group_wait": "10s",
        "group_interval": "1m",
        "repeat_interval": "5m",
        "continue": False,
    }]
    r = httpx.put(f"{BASE}/api/v1/provisioning/policies", auth=AUTH, timeout=10, json=tree)
    if r.status_code not in (200, 202):
        raise SystemExit(f"policy update failed: {r.status_code} {r.text}")
    print("policy route added: severity=warning -> volt-webhook")


def main() -> None:
    wait_ready()
    ensure_folder()
    ensure_rule()
    for slo in SLO_RULES:
        ensure_rule(slo)
    ensure_contact_point()
    ensure_policy()
    print("Grafana alerting provisioned.")


if __name__ == "__main__":
    main()
