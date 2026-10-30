"""TaskFlow DAGs with datasets, task groups, labels and chain()."""
import pendulum

from airflow.datasets import Dataset
from airflow.decorators import dag, task, task_group
from airflow.models.baseoperator import chain
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import get_current_context
from airflow.utils.edgemodifier import Label
from airflow.utils.trigger_rule import TriggerRule

orders = Dataset("s3://lake/orders/")


@dag(schedule_interval="@daily", start_date=pendulum.datetime(2024, 1, 1, tz="UTC"), catchup=False)
def producer():
    @task(outlets=[orders])
    def land():
        ctx = get_current_context()
        return {"day": ctx["ds"], "logical": str(ctx["execution_date"])}

    @task_group
    def checks(payload):
        @task
        def row_count(p):
            return len(p)

        @task(trigger_rule=TriggerRule.ALL_DONE)
        def notify(n, triggering_dataset_events=None):
            print(n)

        notify(row_count(payload))

    done = EmptyOperator(task_id="done")
    payload = land()
    chain(payload, checks(payload))
    payload >> Label("after landing") >> done


@dag(schedule=[orders], start_date=pendulum.datetime(2024, 1, 1, tz="UTC"), catchup=False)
def consumer():
    @task
    def read(**context):
        events = context["triggering_dataset_events"]
        print(events)

    read()


producer()
consumer()
