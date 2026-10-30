import json
import subprocess
import sys

from conftest import ANCHOR, rules, run
from dagshift import Options, migrate_paths
from dagshift.jinja_mask import mask


def test_mask_roundtrip_and_parse():
    import libcst as cst

    src = (
        "{# header #}\n"
        "from airflow import DAG\n"
        "with DAG(dag_id='{{ id }}', schedule={{ sched }}) as d:\n"
        "{% for t in tasks %}\n"
        "    {{ t }} = 1\n"
        "{% endfor %}\n"
    )
    m = mask(src)
    cst.parse_module(m.code)
    assert m.unmask(m.code) == src
    assert "'{{ id }}'" in m.code  # Jinja inside strings is left alone


def test_template_file_migrated():
    res = run("""
        from airflow.operators.bash_operator import BashOperator
        {% for t in tables %}
        {{ t }} = BashOperator(task_id="{{ t }}", bash_command="{% raw %}{{ execution_date }}{% endraw %}")
        {% endfor %}
        """, filename="dag.py.j2")
    assert "{% for t in tables %}" in res.migrated
    assert "airflow.providers.standard.operators.bash" in res.migrated
    assert "{% raw %}{{ logical_date }}{% endraw %}" in res.migrated
    assert "DS505" in rules(res)


def test_dag_factory_yaml():
    res = run("""
        default:
          schedule_interval: "@daily"
          concurrency: 2
        my_dag:
          tasks:
            t1:
              operator: airflow.operators.bash_operator.BashOperator
              bash_command: "echo {{ execution_date }}"
              provide_context: true
        """, filename="dags.yml")
    out = res.migrated
    assert "schedule: \"@daily\"" in out and "max_active_tasks: 2" in out
    assert "airflow.providers.standard.operators.bash.BashOperator" in out
    assert "provide_context" not in out and "{{ logical_date }}" in out
    assert "DS807" in rules(res)


def test_homegrown_yaml_warns_but_keeps_keys():
    res = run("""
        jobs:
          - schedule_interval: "@hourly"
            operator_class: airflow.operators.python.PythonOperator
        """, filename="jobs.yaml")
    assert "schedule_interval" in res.migrated
    assert "airflow.providers.standard.operators.python.PythonOperator" in res.migrated
    assert "DS704" in rules(res, "warn")
    renamed = run('jobs:\n  - schedule_interval: "@hourly"\n', filename="jobs.yaml", rename_config_keys=True)
    assert "schedule:" in renamed.migrated


def test_json_config():
    res = run('{"schedule_interval": "@daily", "cmd": "{{ tomorrow_ds_nodash }}"}', filename="p.json")
    assert "macros.ds_format(macros.ds_add(ds, 1), '%Y-%m-%d', '%Y%m%d')" in res.migrated
    json.loads(res.migrated)  # still valid JSON


def test_platform_files(project):
    run_ = migrate_paths([project], Options(start_date=ANCHOR))
    by_rule = {f.rule_id for f in run_.findings}
    for rid in ("DS801", "DS802", "DS803", "DS804", "DS805", "DS806"):
        assert rid in by_rule, rid


def test_project_migration_is_idempotent(project, tmp_path):
    opts = Options(start_date=ANCHOR)
    first = migrate_paths([project], opts)
    out = tmp_path / "out"
    first.write_to(out)
    second = migrate_paths([out], opts)
    assert second.changed == [], [r.path for r in second.changed]


def test_cli_check_and_migrate(project, tmp_path):
    exe = [sys.executable, "-m", "dagshift.cli"]
    p = subprocess.run(exe + ["check", str(project), "--start-date", "2026-10-29"], capture_output=True, text=True)
    assert p.returncode == 1 and "fixed" in p.stdout
    out = tmp_path / "af3"
    p = subprocess.run(exe + ["migrate", str(project), "--out", str(out), "--start-date", "2026-10-29",
                              "--sarif", str(tmp_path / "r.sarif"), "--report", str(tmp_path / "r.md")],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert (out / "dags" / "classic_operators.py").read_text().count("airflow.providers.standard") >= 3
    sarif = json.loads((tmp_path / "r.sarif").read_text())
    assert sarif["runs"][0]["results"]
    assert "Needs a decision" in (tmp_path / "r.md").read_text()
    p = subprocess.run(exe + ["check", str(out), "--fail-on", "manual"], capture_output=True, text=True)
    assert p.returncode == 1  # manual items remain by design (metadata DB, SubDAG, plugins)
