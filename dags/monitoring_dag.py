"""Daily drift monitoring — Evidently report on the latest production batch.

In production the batch would be derived from the last completed ETL run
(e.g. {{ ds }} for the prior month); the demo dataset has a fixed simulated
window (2026-01..07), so the month is pinned with a comment.
"""

from __future__ import annotations

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.utils.dates import days_ago

# Demo: the simulated production window ends at 2026-07. In prod this would
# be computed as the previous calendar month of the ETL schedule.
MONTH = "2026-07"

with DAG(
    dag_id="daily_monitoring",
    description="Drift + data-quality report for the latest production batch",
    schedule_interval="0 7 * * *",
    start_date=days_ago(1),
    catchup=False,
    tags=["ml", "monitoring"],
    default_args={"retries": 1, "retry_delay": 60},
) as dag:
    run_monitor = BashOperator(
        task_id="run_monitor",
        bash_command=f"python -m credit_decision.monitoring.run_monitor --month {MONTH}",
    )
