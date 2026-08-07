"""Drift-triggered retraining - champion-challenger workflow.

Runs daily after the monitoring DAG; if the latest drift share is above the
business threshold, trains and registers a CANDIDATE model (the `production`
alias is untouched). Candidates are compared offline (shadow scores) and
promoted deliberately, not by a schedule.

In production this DAG would be triggered by the alert itself; a daily
probe keeps the dependency graph simple for the demo.
"""

from __future__ import annotations

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import ShortCircuitOperator
from airflow.utils.dates import days_ago

DRIFT_THRESHOLD = 0.3


def _drift_active() -> bool:
    """True when the latest monitored batch drifted above the threshold."""
    from credit_decision.db import read_sql

    df = read_sql(
        "SELECT (metrics->>'share_drifted')::float AS share FROM monitoring_events "
        "WHERE report_type = 'data_drift' ORDER BY event_id DESC LIMIT 1"
    )
    share = float(df["share"].iloc[0]) if not df.empty else 0.0
    print(f"latest drift share: {share:.3f} (threshold {DRIFT_THRESHOLD})")
    return share >= DRIFT_THRESHOLD


with DAG(
    dag_id="drift_retrain",
    description="Retrain a candidate when drift fires (production alias untouched)",
    schedule_interval="30 7 * * *",  # after daily_monitoring (07:00)
    start_date=days_ago(1),
    catchup=False,
    tags=["ml", "training", "drift"],
    default_args={"retries": 1, "retry_delay": 300},
) as dag:
    check_drift = ShortCircuitOperator(
        task_id="check_drift",
        python_callable=_drift_active,
    )
    retrain_candidate = BashOperator(
        task_id="retrain_candidate",
        bash_command="python -m credit_decision.model.train --candidate",
    )

    check_drift >> retrain_candidate
