"""Things with no automatic fix: SubDAGs and airflow.contrib."""
import pendulum

from airflow import DAG
from airflow.contrib.operators.bigquery_operator import BigQueryOperator
from airflow.operators.subdag import SubDagOperator

with DAG("legacy_removed", start_date=pendulum.datetime(2024, 1, 1, tz="UTC"), schedule=None) as dag:
    SubDagOperator(task_id="section", subdag=None)
    BigQueryOperator(task_id="bq", sql="select 1")
