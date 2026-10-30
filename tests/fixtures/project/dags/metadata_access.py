"""Tasks that query the metadata DB directly (breaks at run time on Airflow 3)."""
from airflow import DAG
from airflow.models import DagRun, TaskInstance, XCom
from airflow.operators.python import PythonOperator
from airflow.utils.session import create_session, provide_session
from airflow.utils.state import State
import pendulum


@provide_session
def count_failures(session=None, **context):
    return session.query(TaskInstance).filter(TaskInstance.state == State.FAILED).count()


def previous_runs(**context):
    runs = DagRun.find(dag_id=context["dag"].dag_id)
    value = XCom.get_one(run_id=context["run_id"], key="x")
    return len(runs), value


def fail_if_upstream_failed(**kwargs):
    for ti in kwargs["dag_run"].get_task_instances():
        if ti.current_state() == State.FAILED:
            raise RuntimeError(ti.task_id)


def purge():
    with create_session() as session:
        session.query(XCom).filter(XCom.dag_id == "x").delete()


with DAG("metadata_access", start_date=pendulum.datetime(2024, 1, 1, tz="UTC"), schedule=None, catchup=False):
    PythonOperator(task_id="count", python_callable=count_failures)
    PythonOperator(task_id="prev", python_callable=previous_runs)
    PythonOperator(task_id="guard", python_callable=fail_if_upstream_failed)
    PythonOperator(task_id="purge", python_callable=purge)
