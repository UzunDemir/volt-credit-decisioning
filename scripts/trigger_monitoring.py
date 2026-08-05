"""Rerun manual__2026-08-04T17:37:54.449317+00:00 once more, live."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

BASE = "http://localhost:8080"
AUTH = ("admin", "volt")
RUN_ID = "manual__2026-08-04T17:37:54.449317+00:00"

r = httpx.post(
    f"{BASE}/api/v1/dags/daily_monitoring/clearTaskInstances",
    auth=AUTH,
    json={"dag_run_id": RUN_ID, "task_ids": ["run_monitor"], "dry_run": False},
    timeout=30,
)
print("clear:", r.status_code)
r = httpx.patch(
    f"{BASE}/api/v1/dags/daily_monitoring/dagRuns/{RUN_ID}",
    auth=AUTH,
    json={"state": "queued"},
    timeout=30,
)
print("queued:", r.status_code)

time.sleep(70)
r = httpx.get(f"{BASE}/api/v1/dags/daily_monitoring/dagRuns/{RUN_ID}", auth=AUTH, timeout=10)
print("state:", r.json().get("state"), "| end:", r.json().get("end_date"))
