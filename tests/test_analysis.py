from conftest import rules, run


def test_metadata_db_access_detected():
    res = run("""
        from airflow.models import DagRun, XCom
        from airflow.utils.session import provide_session

        @provide_session
        def a(session=None):
            return session.query(DagRun).count()

        def b():
            return XCom.get_one(run_id="r", key="k")

        def c(**kw):
            for ti in kw["dag_run"].get_task_instances():
                ti.current_state()
        """)
    assert rules(res, "manual").count("DS401") == 2
    assert rules(res, "manual").count("DS402") == 2


def test_type_hints_and_isinstance_are_not_db_access():
    res = run("""
        from airflow.models import TaskInstance
        def f(ti: TaskInstance) -> None:
            print(isinstance(ti, TaskInstance))
        """)
    assert "DS401" not in rules(res)


def test_parse_time_dynamic_values():
    res = run("""
        import datetime, uuid
        from airflow.operators.bash import BashOperator
        BashOperator(task_id="b", bash_command="x", env={"t": str(datetime.datetime.now())})
        def later():
            return uuid.uuid4()
        """)
    assert rules(res, "warn").count("DS502") == 1


def test_cron_semantics_warn_vs_info():
    warn = run("""
        from airflow import DAG
        from airflow.operators.bash import BashOperator
        with DAG("d", schedule="@daily", catchup=False):
            BashOperator(task_id="b", bash_command="load {{ ds }}")
        """)
    assert "DS503" in rules(warn, "warn")
    info = run('from airflow import DAG\nDAG("d", schedule="@daily", catchup=False)\n')
    assert "DS503" in rules(info, "info")


def test_timedelta_schedule_semantics():
    res = run("""
        from datetime import timedelta
        from airflow import DAG
        DAG("d", schedule=timedelta(hours=6), catchup=False)
        """)
    assert "DS503" in rules(res)


def test_plugin_rules():
    res = run("""
        from airflow.plugins_manager import AirflowPlugin
        class P(AirflowPlugin):
            name = "p"
            appbuilder_views = []
            executors = []
            fastapi_apps = []
        """)
    assert rules(res, "manual").count("DS504") == 2


def test_codegen_string_migrated_recursively():
    res = run('''
        TEMPLATE = """
        from airflow.operators.bash_operator import BashOperator
        BashOperator(task_id="x", bash_command="{{ execution_date }}")
        """
        ''')
    assert "airflow.providers.standard.operators.bash import BashOperator" in res.migrated
    assert "{{ logical_date }}" in res.migrated
    assert "DS601" in rules(res, "fixed")


def test_docstrings_untouched():
    res = run('''
        """Uses airflow.operators.bash_operator.BashOperator and {{ execution_date }}."""
        x = 1
        ''')
    assert res.migrated.startswith('"""Uses airflow.operators.bash_operator.BashOperator and {{ execution_date }}."""')


def test_dataset_in_generated_fstring():
    res = run("""
        from airflow import DAG
        def outlets(names):
            return ", ".join(f'Dataset("{n}")' for n in names)
        """)
    assert """f'Asset("{n}")'""" in res.migrated
