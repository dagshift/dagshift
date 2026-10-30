from conftest import rules, run
from dagshift.jinja_text import rewrite_jinja


def test_jinja_renames_and_expressions():
    text, issues = rewrite_jinja("{{ execution_date }} {{ next_ds }} {{ tomorrow_ds ~ 'x' }} {{ prev_ds }}")
    assert text == "{{ logical_date }} {{ data_interval_end | ds }} {{ (macros.ds_add(ds, 1)) ~ 'x' }} {{ prev_ds }}"
    sev = [i.severity.value for i in issues]
    assert sev.count("fixed") == 3 and sev.count("manual") == 1


def test_jinja_skips_filters_attributes_and_literals():
    text, issues = rewrite_jinja(
        "{{ data_interval_end | ds }} {{ params.execution_date }} {{ var.value.get('execution_date') }}"
    )
    assert text.startswith("{{ data_interval_end | ds }}")
    assert "params.execution_date" in text and "'execution_date'" in text
    assert not [i for i in issues if i.rule_id == "DS301"]


def test_jinja_runtime_attribute_and_xcom_pull():
    text, issues = rewrite_jinja("{{ dag_run.execution_date }} {{ ti.xcom_pull(key='k') }} {{ ti.xcom_pull('t') }}")
    assert "dag_run.logical_date" in text
    assert [i.rule_id for i in issues].count("DS303") == 1


def test_python_context_keys():
    res = run("""
        def cb(context):
            a = context["execution_date"]
            b = context.get("triggering_dataset_events")
            c = context["tomorrow_ds"]
            d = context["prev_execution_date"]
            e = context["dag_run"].execution_date
        """)
    out = res.migrated
    assert 'context["logical_date"]' in out
    assert 'context.get("triggering_asset_events")' in out
    assert 'context["logical_date"].add(days=1).strftime("%Y-%m-%d")' in out
    assert 'context["dag_run"].logical_date' in out
    assert "DS302" in rules(res, "manual")


def test_task_callable_params_renamed_with_references():
    res = run("""
        from airflow.operators.python import PythonOperator
        def report(execution_date, ds, **kw):
            return execution_date.isoformat() + ds
        PythonOperator(task_id="r", python_callable=report)
        """)
    assert "def report(logical_date, ds, **kw):" in res.migrated
    assert "return logical_date.isoformat() + ds" in res.migrated


def test_get_current_context_receiver():
    res = run("""
        from airflow.decorators import task
        from airflow.operators.python import get_current_context
        @task
        def t():
            c = get_current_context()
            return c["execution_date"]
        """)
    assert 'return c["logical_date"]' in res.migrated


def test_strings_in_operator_args():
    res = run("""
        from airflow.operators.bash import BashOperator
        BashOperator(task_id="b", bash_command="echo {{ execution_date }} {{ yesterday_ds_nodash }}")
        """)
    assert '{{ logical_date }}' in res.migrated
    assert "macros.ds_format(macros.ds_add(ds, -1), '%Y-%m-%d', '%Y%m%d')" in res.migrated


def test_fstring_jinja():
    res = run('cmd = f"run {table} {{{{ execution_date }}}}"\n')
    assert res.migrated == 'cmd = f"run {table} {{{{ logical_date }}}}"\n'
