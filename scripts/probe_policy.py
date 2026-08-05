"""Update the drift alert rule SQL to latest-event ordering."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

BASE = "http://localhost:3000"
AUTH = ("admin", "volt")

rules = httpx.get(f"{BASE}/api/v1/provisioning/alert-rules", auth=AUTH, timeout=5).json()
rule = rules[0]
uid = rule["uid"]
rule["data"][0]["model"]["rawSql"] = (
    "SELECT (metrics->>'share_drifted')::float AS share FROM monitoring_events "
    "WHERE report_type = 'data_drift' ORDER BY event_id DESC LIMIT 1"
)
r = httpx.put(f"{BASE}/api/v1/provisioning/alert-rules/{uid}", auth=AUTH, timeout=10, json=rule)
print("rule update:", r.status_code)
