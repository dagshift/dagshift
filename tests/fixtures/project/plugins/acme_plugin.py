"""Custom operator, sensor, hook and a UI plugin, Airflow 2 style."""
from flask import Blueprint

from airflow.hooks.base_hook import BaseHook
from airflow.models import BaseOperator
from airflow.plugins_manager import AirflowPlugin
from airflow.sensors.base import BaseSensorOperator
from airflow.utils.decorators import apply_defaults


class AcmeHook(BaseHook):
    def __init__(self, conn_id="acme_default"):
        super().__init__()
        self.conn = self.get_connection(conn_id)


class AcmeExportOperator(BaseOperator):
    template_fields = ("target",)

    @apply_defaults
    def __init__(self, target, **kwargs):
        super().__init__(**kwargs)
        self.target = target

    def execute(self, context):
        day = context["ds"]
        stamp = context["execution_date"]
        run_stamp = context["dag_run"].execution_date
        prev = context["prev_ds"]
        self.log.info("export %s %s %s %s", day, stamp, run_stamp, prev)


class AcmeReadySensor(BaseSensorOperator):
    def poke(self, context):
        return context["ti"].xcom_pull(task_ids="export") is not None


bp = Blueprint("acme", __name__)


class AcmePlugin(AirflowPlugin):
    name = "acme"
    flask_blueprints = [bp]
    operators = [AcmeExportOperator]
    hooks = [AcmeHook]
