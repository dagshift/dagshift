"""Parse migrated fixtures with real Airflow 3 installs.

Set DAGSHIFT_AIRFLOW_PYTHONS to run, e.g.
    DAGSHIFT_AIRFLOW_PYTHONS="3.0=/venvs/af30/bin/python,3.3=/venvs/af33/bin/python" pytest tests/test_real_airflow.py

Each interpreter needs apache-airflow-core, the task SDK and the standard, common-sql and smtp providers.
"""
import os

import pytest

from conftest import ANCHOR, FIXTURES
from dagshift import Options, migrate_paths, validate

PYTHONS = dict(
    pair.split("=", 1) for pair in os.environ.get("DAGSHIFT_AIRFLOW_PYTHONS", "").split(",") if "=" in pair
)
EXPECTED_MANUAL_FAILURES = {"legacy_removed.py"}  # airflow.contrib + SubDAG: no automatic fix by design


@pytest.mark.skipif(not PYTHONS, reason="set DAGSHIFT_AIRFLOW_PYTHONS to run against real Airflow")
@pytest.mark.parametrize("target", sorted(t for t in PYTHONS if t.startswith("3.")))
def test_migrated_fixtures_load(target, tmp_path):
    python = PYTHONS[target]
    original = validate(FIXTURES / "project" / "dags", python)
    assert original.import_errors, "fixtures should be broken on Airflow 3 before migration"

    run = migrate_paths([FIXTURES / "project"], Options(target=target, start_date=ANCHOR))
    out = tmp_path / "af3"
    run.write_to(out)
    migrated = validate(out / "dags", python)
    assert migrated.fatal is None, migrated.fatal
    failures = set(migrated.import_errors)
    # On 3.0 an in-process DagBag cannot serve top-level airflow.sdk.Variable.get (needs the
    # DAG processor's supervisor); that is a limitation of this check, not of the migration.
    failures = {f for f in failures if "SUPERVISOR_COMMS" not in migrated.import_errors[f]}
    assert failures == EXPECTED_MANUAL_FAILURES, migrated.to_text()
    assert len(migrated.dags) >= 8


AF2_EXAMPLES = os.environ.get("DAGSHIFT_AF2_EXAMPLES")  # path to apache-airflow 2.x airflow/example_dags


@pytest.mark.skipif(not (PYTHONS and AF2_EXAMPLES), reason="set DAGSHIFT_AIRFLOW_PYTHONS and DAGSHIFT_AF2_EXAMPLES")
@pytest.mark.parametrize("target", sorted(t for t in PYTHONS if t.startswith("3.")))
def test_airflow2_example_dags(target, tmp_path):
    """Airflow's own 2.x example DAGs: everything left broken must be a reported manual item."""
    run = migrate_paths([AF2_EXAMPLES], Options(target=target, start_date=ANCHOR))
    out = tmp_path / "examples"
    run.write_to(out)
    res = validate(out, PYTHONS[target])
    assert res.fatal is None, res.fatal
    assert not res.warnings, res.to_text()
    manual_files = {f.path for f in run.findings if f.severity.value == "manual"}
    unexplained = {p for p in res.import_errors if p not in manual_files}
    assert not unexplained, res.to_text()


@pytest.mark.skipif("2.10" not in PYTHONS, reason="needs an Airflow 2.10 interpreter in DAGSHIFT_AIRFLOW_PYTHONS")
def test_two_x_upgrade_removes_deprecation_warnings(tmp_path):
    python = PYTHONS["2.10"]
    before = validate(FIXTURES / "project" / "dags", python)
    run = migrate_paths([FIXTURES / "project"], Options(source="2.7", target="2.10", start_date=ANCHOR))
    run.write_to(tmp_path / "af210")
    after = validate(tmp_path / "af210" / "dags", python)
    assert set(after.dags) == set(before.dags)
    assert set(after.import_errors) == set(before.import_errors)  # contrib file: reported manual item
    leftover = [w for w in after.warnings if w["file"] != "legacy_removed.py"]
    assert len(before.warnings) >= 10 and not leftover, after.to_text()
