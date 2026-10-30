from conftest import rules, run


def test_from_imports_move_and_merge():
    res = run("""
        from airflow import DAG
        from airflow.decorators import dag, task
        from airflow.operators.python import PythonOperator, get_current_context
        """)
    out = res.migrated
    assert "from airflow.sdk import DAG, dag, task, get_current_context" in out
    assert "from airflow.providers.standard.operators.python import PythonOperator" in out
    assert "airflow.decorators" not in out and "from airflow import DAG" not in out


def test_renamed_symbol_updates_references():
    res = run("""
        from airflow.datasets import Dataset
        from airflow.operators.dummy import DummyOperator

        a = Dataset("s3://x")
        t = DummyOperator(task_id="t")

        def f():
            return Dataset("y")
        """)
    out = res.migrated
    assert "from airflow.sdk import Asset" in out
    assert 'a = Asset("s3://x")' in out and 'return Asset("y")' in out
    assert "EmptyOperator(task_id" in out and "DummyOperator" not in out


def test_alias_kept_when_new_name_clashes():
    res = run("""
        from airflow.datasets import Dataset
        Asset = "my own thing"
        x = Dataset("s3://x")
        """)
    assert "from airflow.sdk import Asset as Dataset" in res.migrated
    assert 'x = Dataset("s3://x")' in res.migrated


def test_asname_preserved():
    res = run("from airflow.operators.bash import BashOperator as Bash\nBash(task_id='x', bash_command='true')\n")
    assert "from airflow.providers.standard.operators.bash import BashOperator as Bash" in res.migrated


def test_imports_inside_try_rewritten_in_place():
    res = run("""
        try:
            from airflow.decorators import task
        except ImportError:
            task = None
        """)
    assert "    from airflow.sdk import task\n" in res.migrated
    assert "DS501" in rules(res)


def test_attribute_style_references():
    res = run("""
        import airflow.operators.bash
        airflow.operators.bash.BashOperator(task_id="x", bash_command="true")
        """)
    assert "from airflow.providers.standard.operators.bash import BashOperator" in res.migrated
    assert "import airflow.operators.bash" not in res.migrated
    assert 'BashOperator(task_id="x"' in res.migrated


def test_target_gating_trigger_rule_and_basehook():
    src = """
        from airflow.utils.trigger_rule import TriggerRule
        from airflow.hooks.base import BaseHook
        """
    on30 = run(src, target="3.0").migrated
    on33 = run(src, target="3.3").migrated
    # airflow.sdk has neither TriggerRule nor BaseHook on 3.0
    assert "from airflow.utils.trigger_rule import TriggerRule" in on30
    assert "from airflow.hooks.base import BaseHook" in on30
    assert "from airflow.sdk import TriggerRule, BaseHook" in on33


def test_removed_without_replacement_is_manual():
    res = run("from airflow.operators.subdag import SubDagOperator\nSubDagOperator(task_id='x', subdag=None)\n")
    assert "DS103" in rules(res, "manual")
    assert "from airflow.operators.subdag import SubDagOperator" in res.migrated


def test_contrib_and_legacy_provider_paths_use_airflow2_tables():
    res = run("""
        from airflow.contrib.hooks.gcs_hook import GoogleCloudStorageHook
        from airflow.hooks.postgres_hook import PostgresHook
        from airflow.contrib.operators.bigquery_operator import BigQueryOperator
        from airflow.operators.python import *
        """)
    out = res.migrated
    assert "from airflow.providers.google.cloud.hooks.gcs import GCSHook" in out
    assert "from airflow.providers.postgres.hooks.postgres import PostgresHook" in out
    # BigQueryExecuteQueryOperator is gone from current google provider releases: manual
    assert "from airflow.contrib.operators.bigquery_operator import BigQueryOperator" in out
    assert "DS103" in rules(res, "manual") and rules(res).count("DS104") == 2  # contrib leftover + star import


def test_sql_operators_replaced():
    res = run("""
        from airflow.providers.postgres.operators.postgres import PostgresOperator
        PostgresOperator(task_id="p", postgres_conn_id="pg", sql="select 1")
        """)
    assert 'SQLExecuteQueryOperator(task_id="p", conn_id="pg", sql="select 1")' in res.migrated
    assert "from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator" in res.migrated
    assert "PostgresOperator" not in res.migrated


def test_no_modernize_keeps_working_paths():
    res = run("from airflow import DAG\nfrom airflow.decorators import task\n", modernize=False)
    assert "from airflow import DAG" in res.migrated          # still works: untouched
    assert "from airflow.sdk import task" in res.migrated      # deprecated: moved


def test_extra_moves_for_internal_libraries():
    res = run("from acme.ops import LegacyOp\nLegacyOp()\n", extra_moves={"acme.ops.LegacyOp": "acme.ops3.NewOp"})
    assert "from acme.ops3 import NewOp" in res.migrated
    assert "NewOp()" in res.migrated
    assert "DS101" in rules(res, "fixed")


def test_idempotent():
    src = "from airflow.operators.bash_operator import BashOperator\nfrom airflow import DAG\n"
    once = run(src).migrated
    twice = run(once).migrated
    assert once == twice
