import pytest

from conftest import rules, run
from dagshift import detect_version
from dagshift.kb import KnowledgeBase


# ---- detection ---------------------------------------------------------------------------
def write(tmp_path, name, text):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_detect_requirements_and_constraints(tmp_path):
    write(tmp_path, "requirements.txt", "apache-airflow[google]==2.9.3\n")
    write(tmp_path, "Makefile.txt", "")
    det = detect_version([tmp_path])
    assert (det.version, det.confidence) == ("2.9.3", "pinned")
    write(tmp_path, "ci/install.sh.txt", "")
    write(tmp_path, "constraints.txt", "# https://raw.githubusercontent.com/apache/airflow/constraints-2.9.3/constraints-3.11.txt\n")
    assert detect_version([tmp_path]).minor == "2.9"


def test_detect_from_separate_terraform_repo(tmp_path):
    dags = tmp_path / "dags-repo"
    infra = tmp_path / "infra-repo"
    write(dags, "dags/etl.py", "from airflow.decorators import task\n")
    write(infra, "composer/main.tf", 'image_version = "composer-3-airflow-2.10.5-build.15"\n')
    alone = detect_version([dags])
    assert alone.confidence == "inferred" and alone.version == "2.0"
    both = detect_version([dags], [infra])
    assert (both.version, both.confidence) == ("2.10.5", "pinned")
    assert both.evidence[0].kind == "composer"


def test_detect_conflicts_and_code_inference(tmp_path):
    write(tmp_path, "requirements.txt", "apache-airflow==2.8.1\n")
    write(tmp_path, "Dockerfile", "FROM apache/airflow:2.10.5-python3.11\n")
    write(tmp_path, "docker-compose.yaml", "image: apache/airflow:2.10.5\n")
    det = detect_version([tmp_path])
    assert det.minor == "2.10" and det.conflicts == ["2.8"]
    code = tmp_path / "code"
    write(code, "dag.py", "from airflow.sdk import DAG, Asset\n")
    assert detect_version([code]).version == "3.0"
    assert detect_version([tmp_path / "nothing"]).confidence == "unknown"


def test_downgrade_rejected():
    with pytest.raises(ValueError, match="older than the current version"):
        KnowledgeBase("2.10", source="3.1")


# ---- 2.x -> 2.y ------------------------------------------------------------------------------
LEGACY = """
from airflow import DAG
from airflow.operators.bash_operator import BashOperator
from airflow.operators.dummy import DummyOperator
from airflow.utils.dates import days_ago

def cb(**context):
    return context["execution_date"]

with DAG("d", schedule_interval="@daily", start_date=days_ago(1)) as dag:
    DummyOperator(task_id="start")
    BashOperator(task_id="b", bash_command="echo {{ execution_date }} {{ ti.xcom_pull(key='k') }}", provide_context=True)
"""


def test_two_x_to_two_y_stays_on_airflow_2_apis():
    res = run(LEGACY, source="2.7", target="2.10")
    out = res.migrated
    assert "airflow.sdk" not in out and "providers.standard" not in out
    assert "from airflow.operators.bash import BashOperator" in out
    assert "from airflow.operators.empty import EmptyOperator" in out
    assert 'schedule="@daily"' in out and "pendulum.datetime(2026, 10, 28" in out
    assert "{{ logical_date }}" in out and 'context["logical_date"]' in out
    assert "provide_context" not in out
    # Airflow 3 behaviour changes do not apply to a 2.x upgrade
    for rid in ("DS208", "DS209", "DS503", "DS303", "DS401"):
        assert rid not in rules(res), rid
    assert "deprecated" in next(f.message for f in res.findings if f.rule_id == "DS201")


def test_two_x_without_modernize_only_fixes_what_breaks_or_warns():
    res = run(LEGACY, source="2.7", target="2.10", modernize=False)
    out = res.migrated
    assert "schedule_interval" in out and "{{ execution_date }}" in out  # still valid on 2.10
    assert "from airflow.operators.bash import BashOperator" in out  # deprecated path: moved


def test_three_x_to_three_y():
    src = """
from airflow.datasets import Dataset
from airflow.sdk import DAG
DAG("d", schedule=[Dataset("s3://x")])
"""
    res = run(src, source="3.0", target="3.3")
    assert "from airflow.sdk import Asset, DAG" in res.migrated and "Dataset" not in res.migrated
    assert "DS208" not in rules(res) and "DS209" not in rules(res)  # already on Airflow 3


def test_crossing_three_applies_behaviour_rules():
    res = run('from airflow import DAG\nDAG("d")\n', source="2.10", target="3.3")
    assert "schedule=timedelta(days=1)" in res.migrated
    assert "DS209" in rules(res)


def test_kb_chains_legacy_paths_per_target():
    kb30 = KnowledgeBase("3.0")
    assert kb30.lookup("airflow.hooks.base_hook.BaseHook").new == "airflow.hooks.base.BaseHook"
    kb33 = KnowledgeBase("3.3")
    assert kb33.lookup("airflow.hooks.base_hook.BaseHook").new == "airflow.sdk.BaseHook"
    kb27 = KnowledgeBase("2.7")
    assert kb27.lookup("airflow.operators.bash_operator.BashOperator").new == "airflow.operators.bash.BashOperator"
