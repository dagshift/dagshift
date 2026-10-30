"""docs/rules.md is generated from the rule catalog and real engine runs; keep it in sync."""
import datetime as dt
import importlib.util
from pathlib import Path

from dagshift import Options, detect_version, migrate_source
from dagshift.rules_catalog import APPLIES, RULES

ROOT = Path(__file__).resolve().parents[1]


def _gen():
    spec = importlib.util.spec_from_file_location("gen_rules_doc", ROOT / "tools" / "gen_rules_doc.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rules_doc_is_current_and_every_example_fires():
    text, problems = _gen().render()
    assert not problems, problems
    assert (ROOT / "docs" / "rules.md").read_text() == text, "run: python tools/gen_rules_doc.py"


def test_every_rule_says_where_it_applies():
    assert set(APPLIES) == set(RULES)
    assert set(APPLIES.values()) <= {"any", "to 3.x", "across 3.0"}


def test_start_date_accepts_strings_and_datetimes():
    code = "from airflow.utils.dates import days_ago\nx = days_ago(1)\n"
    for value in ("2026-01-02", dt.datetime(2026, 1, 2, 9, 30), dt.date(2026, 1, 2)):
        res = migrate_source(code, "dag.py", Options(source="2.10", target="3.3", start_date=value))
        assert "pendulum.datetime(2026, 1, 1" in res.migrated


def test_evidence_from_another_repo_is_labelled_with_that_repo(tmp_path):
    dags = tmp_path / "dags"
    dags.mkdir()
    (dags / "etl.py").write_text("from airflow import DAG\n")
    infra = tmp_path / "platform-terraform" / "composer"
    infra.mkdir(parents=True)
    (infra / "main.tf").write_text('image_version = "composer-3-airflow-2.9.3-build.10"\n')
    det = detect_version([dags], extra=[tmp_path / "platform-terraform"])
    assert det.version == "2.9.3"
    assert det.evidence[0].file == "platform-terraform/composer/main.tf"
