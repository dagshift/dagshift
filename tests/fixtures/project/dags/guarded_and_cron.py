"""Guarded imports, a cron schedule read through {{ ds }}, and parse-time values."""
import time

try:
    from airflow.decorators import task
    from airflow.utils.state import State
except ImportError:  # local tooling without Airflow
    def task(fn=None, **_kw):
        return fn

from airflow import DAG
from airflow.operators.bash import BashOperator
import pendulum

with DAG(
    dag_id="partitioned_load",
    start_date=pendulum.datetime(2024, 1, 1, tz="America/New_York"),
    schedule_interval="30 4 * * *",
    catchup=False,
    sla_miss_callback=lambda *a: None,
) as dag:
    BashOperator(
        task_id="load_partition",
        bash_command="load --partition {{ ds_nodash }} --since {{ prev_ds }}",
        sla=pendulum.duration(hours=1),
        params={"built_at": int(time.time())},
    )

    @task
    def audit(**context):
        print(context["dag_run"].execution_date, context.get("execution_date"))

    audit()
