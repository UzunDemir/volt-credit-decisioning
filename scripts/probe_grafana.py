"""Probe Grafana datasource query path — why do panels show No data?"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

BASE = "http://localhost:3000"
AUTH = ("admin", "volt")

queries = [
    ("drift share (latest)", "SELECT (metrics->>'share_drifted')::float AS value FROM monitoring_events WHERE report_type = 'data_drift' ORDER BY batch_name DESC LIMIT 1"),
    ("drift by month", "SELECT batch_name AS metric, (metrics->>'share_drifted')::float AS value FROM monitoring_events WHERE report_type = 'data_drift' ORDER BY batch_name"),
    ("decisions ts", "SELECT $__timeGroup(decided_at, '1d') AS time, count(*) AS approvals FROM decisions WHERE $__timeFilter(decided_at) GROUP BY 1 ORDER BY 1"),
    ("decisions recent", "SELECT application_id, model_version, score, decision, decided_at FROM decisions ORDER BY decision_id DESC LIMIT 50"),
]

for name, sql in queries:
    body = {
        "from": "now-90d", "to": "now",
        "queries": [{
            "refId": "A", "datasource": {"type": "postgres", "uid": "voltdb"},
            "queryType": "table", "format": "table",
            "rawQuery": True, "rawSql": sql,
            "intervalMs": 1000, "maxDataPoints": 100,
        }],
    }
    r = httpx.post(f"{BASE}/api/ds/query", auth=AUTH, json=body, timeout=30)
    ok = r.status_code == 200 and "error" not in r.text.lower()
    print(f"--- {name}: status={r.status_code} ok={ok}")
    print(r.text[:600])
