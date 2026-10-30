"""Module-style imports: airflow.operators.bash.BashOperator(...) and models.DAG(...)."""
import pendulum

import airflow.operators.bash
from airflow import models

with models.DAG(
    dag_id="attribute_imports",
    start_date=pendulum.datetime(2024, 1, 1, tz="UTC"),
    schedule=None,
    catchup=False,
):
    airflow.operators.bash.BashOperator(task_id="hello", bash_command="echo hello")
