"""One file, many DAGs: generated in a loop from a list (no explicit schedule)."""
from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.sensors.external_task_sensor import ExternalTaskSensor

TABLES = ["customers", "orders", "payments"]

for table in TABLES:
    dag_id = f"load_{table}"
    with DAG(dag_id, start_date=datetime(2024, 1, 1), tags=["generated"]) as dag:
        wait = ExternalTaskSensor(
            task_id="wait_for_upstream",
            external_dag_id="ingest",
            external_task_id=None,
            mode="reschedule",
        )
        load = BashOperator(
            task_id=f"load_{table}",
            bash_command=f"load.sh {table} {{{{ ds }}}} {{{{ yesterday_ds }}}}",
            env={"STAMP": str(datetime.now())},
        )
        wait >> load
    globals()[dag_id] = dag
