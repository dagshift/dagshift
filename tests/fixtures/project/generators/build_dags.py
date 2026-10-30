"""Code generator: writes DAG files from Python string templates."""
from pathlib import Path

DAG_TEMPLATE = '''
from airflow import DAG
from airflow.operators.python_operator import PythonOperator
from airflow.utils.dates import days_ago

with DAG("{dag_id}", start_date=days_ago(1), schedule_interval="@hourly") as dag:
    PythonOperator(task_id="run", python_callable=print, provide_context=True)
'''

OPERATOR_PATH = "airflow.operators.bash_operator.BashOperator"


def outlets(names):
    return "[" + ", ".join(f'Dataset("{n}")' for n in names) + "]"


def build(out: Path, dag_ids):
    for dag_id in dag_ids:
        (out / f"{dag_id}.py").write_text(DAG_TEMPLATE.replace("{dag_id}", dag_id))
