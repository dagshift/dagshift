from conftest import rules, run


def test_days_ago_literal_becomes_fixed_date():
    res = run("""
        from airflow.utils.dates import days_ago
        start = days_ago(2)
        later = days_ago(1, hour=6)
        """)
    assert 'start = pendulum.datetime(2026, 10, 27, tz="UTC")' in res.migrated
    assert 'later = pendulum.datetime(2026, 10, 28, 6, tz="UTC")' in res.migrated
    assert "import pendulum" in res.migrated and "days_ago" not in res.migrated


def test_days_ago_module_style_and_dynamic_argument():
    res = run("""
        from airflow.utils import dates
        N = 3
        start = dates.days_ago(N)
        """)
    assert 'pendulum.today("UTC").add(days=-(N))' in res.migrated
    assert "from airflow.utils import dates" not in res.migrated
    assert "DS502" in rules(res, "warn")


def test_dag_arguments_renamed_and_positional_converted():
    res = run("""
        from airflow import DAG
        DAG("d", "desc", schedule_interval="@daily", concurrency=3, default_view="tree",
            orientation="LR", fail_stop=True, catchup=False)
        """)
    out = res.migrated
    assert 'description="desc"' in out and 'schedule="@daily"' in out
    assert "max_active_tasks=3" in out and "fail_fast=True" in out
    assert "default_view" not in out and "orientation" not in out


def test_timetable_argument_becomes_schedule():
    res = run("""
        from airflow import DAG
        from airflow.timetables.interval import CronDataIntervalTimetable
        DAG("d", timetable=CronDataIntervalTimetable("@daily", timezone="UTC"), catchup=False)
        """)
    assert "schedule=CronDataIntervalTimetable(" in res.migrated


def test_missing_schedule_preserved_as_daily():
    res = run("""
        from airflow import DAG
        DAG("d", catchup=False)
        """)
    assert "schedule=timedelta(days=1)" in res.migrated
    assert "from datetime import timedelta" in res.migrated
    off = run('from airflow import DAG\nDAG("d", catchup=False)\n', preserve_schedule_default=False)
    assert "schedule=" not in off.migrated and "DS208" in rules(off, "warn")


def test_dag_decorator_and_catchup_pin():
    res = run("""
        from airflow.decorators import dag
        @dag(schedule=None)
        def pipeline():
            pass
        """, pin_catchup=True)
    assert "@dag(schedule=None, catchup=True)" in res.migrated


def test_operator_arguments():
    res = run("""
        from airflow.operators.python import PythonOperator
        from airflow.operators.trigger_dagrun import TriggerDagRunOperator
        PythonOperator(task_id="a", python_callable=print, provide_context=True, task_concurrency=2)
        TriggerDagRunOperator(task_id="b", trigger_dag_id="x", execution_date="{{ ds }}")
        """)
    out = res.migrated
    assert "provide_context" not in out and "max_active_tis_per_dag=2" in out
    assert 'logical_date="{{ ds }}"' in out


def test_task_decorator_provide_context_and_partial():
    res = run("""
        from airflow.decorators import task
        from airflow.operators.bash import BashOperator
        @task(provide_context=True, retries=1)
        def t(**kw):
            pass
        BashOperator.partial(task_id="b", provide_context=True).expand(bash_command=["a"])
        """)
    assert "@task(retries=1)" in res.migrated
    assert 'BashOperator.partial(task_id="b").expand' in res.migrated


def test_default_args_dict_by_name():
    res = run("""
        from airflow import DAG
        args = {"owner": "x", "provide_context": True, "task_concurrency": 1, "sla": None}
        DAG("d", default_args=args, schedule=None, catchup=False)
        """)
    assert '"provide_context"' not in res.migrated
    assert '"max_active_tis_per_dag": 1' in res.migrated
    assert "DS206" in rules(res, "warn")


def test_variable_get_default_var():
    res = run('from airflow.models import Variable\nVariable.get("k", default_var="d")\n')
    assert 'Variable.get("k", default="d")' in res.migrated


def test_apply_defaults_removed():
    res = run("""
        from airflow.models import BaseOperator
        from airflow.utils.decorators import apply_defaults

        class Op(BaseOperator):
            @apply_defaults
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
        """)
    assert "apply_defaults" not in res.migrated
    assert "from airflow.sdk import BaseOperator" in res.migrated


def test_keep_cron_data_intervals_per_target():
    src = """
        from airflow import DAG
        import pendulum
        DAG("d", schedule="0 3 * * *", start_date=pendulum.datetime(2024, 1, 1, tz="Europe/Paris"), catchup=False)
        """
    on33 = run(src, keep_cron_data_intervals=True).migrated
    assert "CronDataIntervalTimetable('0 3 * * *', timezone='Europe/Paris')" in on33
    assert "from airflow.sdk import DAG, CronDataIntervalTimetable" in on33
    on30 = run(src, target="3.0", keep_cron_data_intervals=True).migrated
    assert "from airflow.timetables.interval import CronDataIntervalTimetable" in on30
