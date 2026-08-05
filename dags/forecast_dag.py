"""Monthly portfolio forecast - cohort default rate, Holt-Winters.

Feeds finance planning (provisioning, pricing); artifacts logged to MLflow.
"""

from __future__ import annotations

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.utils.dates import days_ago

with DAG(
    dag_id="monthly_forecast",
    description="Portfolio default-rate forecast for the next 6 months",
    schedule_interval="0 4 1 * *",  # 1st of month 04:00
    start_date=days_ago(30),
    catchup=False,
    tags=["ml", "finance"],
    default_args={"retries": 1, "retry_delay": 300},
) as dag:
    forecast = BashOperator(
        task_id="forecast_portfolio",
        bash_command="python -m credit_decision.model.forecast",
    )
