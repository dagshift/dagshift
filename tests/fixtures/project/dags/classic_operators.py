"""Classic Airflow 2 DAG: pre-2.0 import paths, days_ago, schedule_interval, provide_context."""
from datetime import timedelta

from airflow import DAG
from airflow.models import Variable
from airflow.operators.bash_operator import BashOperator
from airflow.operators.dummy_operator import DummyOperator
from airflow.operators.python_operator import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.utils.dates import days_ago

default_args = {
    "owner": "data-eng",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "provide_context": True,
    "task_concurrency": 1,
}


def extract(**context):
    run_date = context["execution_date"]
    next_day = context["next_ds"]
    context["ti"].xcom_push(key="rows", value=[run_date.isoformat(), next_day])


def report(execution_date, **kwargs):
    rows = kwargs["ti"].xcom_pull(key="rows")
    print(execution_date, rows)


with DAG(
    "classic_operators",
    "Nightly extract and report",
    default_args=default_args,
    start_date=days_ago(2),
    schedule_interval="0 2 * * *",
    concurrency=4,
    default_view="graph",
    tags=["example"],
) as dag:
    start = DummyOperator(task_id="start")

    extract_task = PythonOperator(
        task_id="extract",
        python_callable=extract,
        provide_context=True,
    )

    report_task = PythonOperator(task_id="report", python_callable=report, provide_context=True)

    show = BashOperator(
        task_id="show",
        bash_command=(
            "echo run={{ execution_date }} next={{ next_ds }} tomorrow={{ tomorrow_ds_nodash }} "
            "rows={{ ti.xcom_pull(key='rows') }} env=" + Variable.get("env", default_var="dev")
        ),
    )

    downstream = TriggerDagRunOperator(
        task_id="trigger_downstream",
        trigger_dag_id="downstream",
        execution_date="{{ execution_date }}",
    )

    start >> extract_task >> report_task >> show >> downstream
