"""Weekly retraining of the credit scoring champion.

In production retraining would be *drift-triggered* (alert from the
monitoring DAG); a weekly cadence is the conservative baseline.
"""

from __future__ import annotations

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.utils.dates import days_ago

with DAG(
    dag_id="weekly_retrain",
    description="Retrain champion, register to MLflow registry, update production alias",
    schedule_interval="0 3 * * 1",  # Monday 03:00
    start_date=days_ago(7),
    catchup=False,
    tags=["ml", "training"],
    default_args={"retries": 1, "retry_delay": 300},
) as dag:
    retrain = BashOperator(
        task_id="train_and_register",
        bash_command="python -m credit_decision.model.train",
    )
