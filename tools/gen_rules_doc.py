"""Generate docs/rules.md from the rule catalog, running every example through the real engine.

    python tools/gen_rules_doc.py            # writes docs/rules.md
    python tools/gen_rules_doc.py --check    # fails if docs/rules.md is out of date (CI)

Each example is migrated with dagshift itself, so the diff and the message in the docs are exactly what
users get. An example that no longer produces its rule fails the script.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path
from textwrap import dedent

from dagshift import Options, migrate_source
from dagshift.rules_catalog import APPLIES, RULES

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "rules.md"

# rule -> (file name, source text, current version, target version)
EX: dict[str, tuple[str, str, str, str]] = {
    "DS101": ("dags/etl.py", """
        from airflow.operators.bash_operator import BashOperator
        from airflow.operators.python_operator import PythonOperator
        """, "2.10", "3.3"),
    "DS102": ("dags/etl.py", """
        from airflow import DAG
        from airflow.decorators import task
        """, "2.10", "3.3"),
    "DS103": ("dags/etl.py", """
        from airflow.utils.dates import date_range
        """, "2.10", "3.3"),
    "DS104": ("dags/etl.py", """
        from airflow.operators.subdag import SubDagOperator
        """, "2.10", "3.3"),
    "DS201": ("dags/etl.py", """
        from airflow import DAG
        from airflow.utils.dates import days_ago

        with DAG("etl", start_date=days_ago(2), schedule="@daily"):
            pass
        """, "2.10", "3.3"),
    "DS202": ("plugins/ops.py", """
        from airflow.models import BaseOperator
        from airflow.utils.decorators import apply_defaults


        class LoadOperator(BaseOperator):
            @apply_defaults
            def __init__(self, table, **kwargs):
                super().__init__(**kwargs)
                self.table = table
        """, "2.10", "3.3"),
    "DS203": ("dags/etl.py", """
        from datetime import datetime
        from airflow import DAG

        dag = DAG("etl", "Nightly load", schedule_interval="@daily", concurrency=4,
                  start_date=datetime(2024, 1, 1))
        """, "2.7", "2.10"),
    "DS204": ("dags/etl.py", """
        from airflow.operators.python import PythonOperator

        def load(**context):
            pass

        PythonOperator(task_id="load", python_callable=load, provide_context=True, task_concurrency=1)
        """, "2.10", "3.3"),
    "DS205": ("dags/etl.py", """
        from airflow.models import Variable

        bucket = Variable.get("bucket", default_var="raw")
        """, "2.10", "3.3"),
    "DS206": ("dags/etl.py", """
        from datetime import datetime, timedelta
        from airflow import DAG

        def alert(*args):
            pass

        with DAG("etl", start_date=datetime(2024, 1, 1), schedule="@daily", sla_miss_callback=alert,
                 default_args={"sla": timedelta(hours=1)}):
            pass
        """, "2.10", "3.3"),
    "DS208": ("dags/etl.py", """
        from datetime import datetime
        from airflow import DAG

        with DAG("etl", start_date=datetime(2024, 1, 1)):
            pass
        """, "2.10", "3.3"),
    "DS209": ("dags/etl.py", """
        from datetime import datetime
        from airflow import DAG

        with DAG("etl", start_date=datetime(2024, 1, 1), schedule="@daily"):
            pass
        """, "2.10", "3.3"),
    "DS210": ("dags/etl.py", """
        from airflow import DAG
        from config import load

        for cfg in load():
            globals()[cfg["dag_id"]] = DAG(**cfg)
        """, "2.10", "3.3"),
    "DS211": ("dags/etl.py", """
        from airflow.providers.postgres.operators.postgres import PostgresOperator

        PostgresOperator(task_id="vacuum", postgres_conn_id="dwh", sql="VACUUM")
        """, "2.10", "3.3"),
    "DS301": ("dags/etl.py", """
        from airflow.operators.bash import BashOperator

        BashOperator(task_id="load", bash_command="load.sh {{ execution_date }} {{ next_ds }} {{ prev_ds }}")
        """, "2.10", "3.3"),
    "DS302": ("dags/etl.py", """
        def extract(execution_date, **context):
            next_day = context["next_ds"]
            return execution_date, next_day
        """, "2.10", "3.3"),
    "DS303": ("dags/etl.py", """
        def report(ti, **context):
            return ti.xcom_pull(key="rows")
        """, "2.10", "3.3"),
    "DS304": ("dags/etl.py", """
        def report(dag_run, **context):
            return dag_run.execution_date
        """, "2.10", "3.3"),
    "DS401": ("dags/etl.py", """
        from airflow.models import TaskInstance
        from airflow.utils.session import provide_session

        @provide_session
        def failures(session=None, **context):
            return session.query(TaskInstance).filter(TaskInstance.state == "failed").count()
        """, "2.10", "3.3"),
    "DS402": ("dags/etl.py", """
        def check(dag_run, **context):
            return [ti.task_id for ti in dag_run.get_task_instances()]
        """, "2.10", "3.3"),
    "DS501": ("dags/etl.py", """
        try:
            from airflow.operators.bash import BashOperator
        except ImportError:
            from airflow.operators.bash_operator import BashOperator
        """, "2.10", "3.3"),
    "DS502": ("dags/etl.py", """
        from datetime import datetime
        from airflow import DAG

        with DAG("etl", start_date=datetime(2024, 1, 1), schedule="@daily",
                 tags=[datetime.now().strftime("%Y%m%d")]):
            pass
        """, "2.10", "3.3"),
    "DS503": ("dags/etl.py", """
        from datetime import datetime
        from airflow import DAG
        from airflow.operators.bash import BashOperator

        with DAG("etl", start_date=datetime(2024, 1, 1), schedule="30 4 * * *", catchup=False):
            BashOperator(task_id="load", bash_command="load.sh {{ ds }}")
        """, "2.10", "3.3"),
    "DS504": ("plugins/acme.py", """
        from airflow.plugins_manager import AirflowPlugin
        from flask import Blueprint

        bp = Blueprint("acme", __name__)

        class AcmePlugin(AirflowPlugin):
            name = "acme"
            flask_blueprints = [bp]
        """, "2.10", "3.3"),
    "DS505": ("templates/table_dag.py.j2", """
        from airflow import DAG
        from airflow.operators.bash_operator import BashOperator

        with DAG("{{ name }}", schedule_interval="{{ schedule }}") as dag:
        {% for t in tables %}
            BashOperator(task_id="load_{{ t }}", bash_command="load.sh {{ t }} {{ '{{' }} ds {{ '}}' }}")
        {% endfor %}
        """, "2.10", "3.3"),
    "DS601": ("generators/build.py", '''
        TEMPLATE = """
        from airflow.operators.bash_operator import BashOperator
        BashOperator(task_id="{name}", bash_command="run {{{{ execution_date }}}}")
        """
        ''', "2.10", "3.3"),
    "DS602": ("generators/registry.py", """
        OPERATORS = {"bash": "airflow.operators.bash_operator.BashOperator"}
        """, "2.10", "3.3"),
    "DS701": ("configs/pipelines.yaml", """
        pipelines:
          - name: customers
            operator: airflow.operators.bash_operator.BashOperator
            command: "load.sh {{ execution_date }}"
        """, "2.10", "3.3"),
    "DS703": ("configs/dag_factory.yml", """
        default:
          default_args:
            owner: data-eng
          schedule_interval: "@daily"
        customers:
          tasks:
            load:
              operator: airflow.operators.bash_operator.BashOperator
              bash_command: load.sh
        """, "2.10", "3.3"),
    "DS704": ("configs/pipeline.json", """
        {"dag_id": "json_pipeline", "schedule_interval": "@daily", "concurrency": 2}
        """, "2.10", "3.3"),
    "DS801": ("requirements.txt", """
        apache-airflow==2.10.5
        apache-airflow-providers-google==10.26.0
        """, "2.10", "3.3"),
    "DS803": ("dags/.airflowignore", """
        scratch_.*
        .*_test\\.py
        """, "2.10", "3.3"),
    "DS804": ("infra/main.tf", """
        resource "google_composer_environment" "prod" {
          config {
            software_config {
              image_version = "composer-2.9.7-airflow-2.10.5"
            }
          }
        }
        """, "2.10", "3.3"),
    "DS805": ("infra/mwaa.tf", """
        resource "aws_mwaa_environment" "prod" {
          airflow_version = "2.10.3"
        }
        """, "2.10", "3.3"),
    "DS806": ("airflow.cfg", """
        [webserver]
        rbac = True
        [core]
        dag_concurrency = 16
        """, "2.10", "3.3"),
    "DS807": ("configs/dag_factory.yml", """
        default:
          default_args:
            owner: data-eng
        customers:
          schedule: "@daily"
          tasks:
            load:
              operator: airflow.providers.standard.operators.bash.BashOperator
              bash_command: load.sh
        """, "2.10", "3.3"),
}

# rules whose example only shows up together with the rules applied inside it
WITH = {"DS601": ["DS1", "DS3"]}

FENCE = {".py": "python", ".j2": "jinja", ".yaml": "yaml", ".yml": "yaml", ".json": "json", ".tf": "hcl",
         ".cfg": "ini", ".txt": "text"}


def _lang(name: str) -> str:
    return next((v for k, v in FENCE.items() if name.endswith(k)), "text")


def example(rule: str) -> tuple[str | None, list[str]]:
    """Return (markdown, problems)."""
    if rule not in EX:
        return None, []
    name, src, source, target = EX[rule]
    text = dedent(src).lstrip("\n")
    # only this rule, so the diff shows its own change and nothing else
    res = migrate_source(text, name, Options(source=source, target=target, start_date="2026-01-01", select=[rule, *WITH.get(rule, [])]))
    hits = [f for f in res.findings if f.rule_id == rule]
    if not hits:
        got = sorted({f.rule_id for f in res.findings})
        return None, [f"{rule}: example produced {got or 'no findings'}"]
    out = [f"Example, Airflow {source} to {target}, `{name}`:", ""]
    if res.migrated != res.original:
        diff = difflib.unified_diff(res.original.splitlines(), res.migrated.splitlines(), "before", "after",
                                    lineterm="", n=3)
        out += ["```diff", *list(diff)[2:], "```", ""]
    else:
        out += [f"```{_lang(name)}", text.rstrip("\n"), "```", ""]
    for f in hits[:3]:
        out.append(f"> **{f.severity.value}**, line {f.line}: {f.message}")
        if f.recipe and "\n" in f.recipe:
            head, _, rest = f.recipe.partition("\n")
            out += [">", f"> How to fix: {head}", ">", "> ```text"]
            out += [f"> {ln}".rstrip() for ln in rest.splitlines()]
            out.append("> ```")
        elif f.recipe:
            out += [">", f"> How to fix: {f.recipe}"]
        out.append("")
    return "\n".join(out), []


APPLIES_TEXT = {
    "any": "any upgrade (the knowledge base decides per target what is removed or deprecated)",
    "to 3.x": "upgrades whose target is Airflow 3.0 or newer",
    "across 3.0": "upgrades from Airflow 2 to Airflow 3",
}

AREAS = [
    ("DS1", "Import paths"), ("DS2", "Removed functions and changed signatures"), ("DS3", "Task context and templates"),
    ("DS4", "Metadata database access"), ("DS5", "Behaviour changes"), ("DS6", "Code generators"),
    ("DS7", "YAML and JSON configs"), ("DS8", "Environment and platform"),
]


def render() -> tuple[str, list[str]]:
    problems: list[str] = []
    lines = [
        "# dagshift rules",
        "",
        "Generated by `tools/gen_rules_doc.py`; every example below is run through dagshift when the page is built.",
        "",
        "Severity says what happened to a finding:",
        "",
        "* **fixed**: rewritten, behaviour preserved",
        "* **manual**: breaks on the target and has no safe rewrite; a recipe is attached",
        "* **warn**: still imports, but behaves differently on the target",
        "* **info**: worth knowing before cutover",
        "",
        "Rules only fire when they matter for the upgrade you picked. 2.7 to 2.10 gets the 2.x deprecations;",
        "2.10 to 3.3 gets those plus the Airflow 3 changes; 3.0 to 3.3 gets what changed inside Airflow 3.",
        "",
        "Turn a rule off with `--ignore DS102`, `ignore = [\"DS102\"]` in `[tool.dagshift]`, or",
        "`# dagshift: ignore[DS102]` on a line.",
        "",
    ]
    for prefix, title in AREAS:
        ids = [r for r in RULES if r.startswith(prefix)]
        anchor = f"{prefix.lower()}xx-{title.lower().replace(' ', '-')}"
        lines.append(f"* [{title}](#{anchor}): " + ", ".join(f"[{r}](#{r.lower()})" for r in ids))
    lines.append("")
    for prefix, title in AREAS:
        lines += [f"## {prefix}xx {title}", ""]
        for rid, (name, help_) in RULES.items():
            if not rid.startswith(prefix):
                continue
            lines += [f"### {rid}", "", f"`{name}`: {help_}", "",
                      f"Applies to: {APPLIES_TEXT[APPLIES.get(rid, 'any')]}.", ""]
            md, errs = example(rid)
            problems += errs
            if md:
                lines += [md]
    return "\n".join(lines).rstrip("\n") + "\n", problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    text, problems = render()
    for p in problems:
        print("example problem:", p, file=sys.stderr)
    if args.check:
        current = OUT.read_text() if OUT.exists() else ""
        if current != text:
            print("docs/rules.md is out of date: run python tools/gen_rules_doc.py", file=sys.stderr)
            return 1
        return 1 if problems else 0
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(RULES)} rules)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
