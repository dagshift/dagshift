import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FIXTURES

ROOT = str((FIXTURES / "project").resolve())


@pytest.fixture
def server():
    proc = subprocess.Popen([sys.executable, "-m", "dagshift", "serve"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    counter = iter(range(1, 10_000))

    def call(method, **params):
        rid = next(counter)
        proc.stdin.write(json.dumps({"id": rid, "method": method, "params": params}) + "\n")
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        assert resp["id"] == rid
        return resp

    yield call
    if proc.poll() is None:
        call("shutdown")
        proc.wait(10)


def test_initialize(server):
    res = server("initialize")["result"]
    assert [t["target"] for t in res["targets"]] == ["2.7", "2.8", "2.9", "2.10", "2.11", "3.0", "3.1", "3.2", "3.3"]
    assert "2.4" in res["sources"]
    assert "DS101" in res["rules"]


def test_check_returns_absolute_paths_and_no_bodies(server):
    res = server("check", root=ROOT, options={"startDate": "2026-10-29"})["result"]
    assert res["summary"]["files_changed"] >= 10
    paths = {f["path"] for f in res["files"]}
    # the engine returns native absolute paths (backslashes on Windows), so build the expectation the same way
    assert str(Path(ROOT) / "dags" / "classic_operators.py") in paths
    assert all("migrated" not in f for f in res["files"])
    assert all(f["findings"][0]["absPath"] == f["path"] for f in res["files"] if f["findings"])
    assert res["projectFindings"][0]["absPath"].endswith("requirements.txt")


def test_migrate_document_uses_unsaved_text(server):
    text = "from airflow.operators.bash_operator import BashOperator\n"
    res = server("migrateDocument", path=f"{ROOT}/dags/new_file.py", text=text, root=ROOT)["result"]
    assert res["changed"] and "airflow.providers.standard.operators.bash" in res["migrated"]
    assert res["findings"][0]["rule_id"] == "DS101"


def test_migrate_document_ignores_unknown_files(server):
    res = server("migrateDocument", path=f"{ROOT}/README.md", text="# hi")["result"]
    assert res["kind"] is None and res["findings"] == []


def test_options_and_errors(server):
    res = server("migrateDocument", path=f"{ROOT}/x.py", root=ROOT, options={"target": "3.0"},
                 text="from airflow.utils.trigger_rule import TriggerRule\n")["result"]
    assert not res["changed"]  # airflow.sdk has no TriggerRule on 3.0
    assert server("check", root=ROOT, options={"nope": 1})["error"]["message"] == "unknown option 'nope'"
    assert server("doesNotExist")["error"]["type"] == "MethodNotFound"


def test_detect_and_source_in_check(server):
    det = server("detect", root=ROOT)["result"]
    assert det["version"] == "2.10.5" and det["confidence"] == "pinned"
    res = server("check", root=ROOT, options={"target": "2.11"})["result"]
    assert res["source"] == "2.10" and res["target"] == "2.11"
    assert res["detection"]["evidence"][0]["file"]


def test_migrate_files_and_report(server):
    files = [{"path": f"{ROOT}/dags/classic_operators.py"}, {"path": f"{ROOT}/configs/dag_factory.yml"}]
    res = server("migrateFiles", root=ROOT, files=files)["result"]["files"]
    assert all(f["changed"] and f["migrated"] for f in res)
    md = server("report", root=ROOT, format="md")["result"]["text"]
    assert md.startswith("# Airflow 2.10 to 3.3 upgrade report")


def _copy_project(tmp_path):
    import shutil

    src = tmp_path / "project"
    shutil.copytree(ROOT, src)
    return src


def test_write_copy_leaves_originals_and_marks_the_copy(server, tmp_path):
    src = _copy_project(tmp_path)
    before = {p: p.read_text() for p in src.rglob("*.py")}
    out = tmp_path / "project-airflow-3.3"
    res = server("writeCopy", root=str(src), outDir=str(out), options={"startDate": "2026-10-29"})["result"]
    assert res["ok"] and res["target"] == "3.3" and res["source"] == "2.10"
    assert {p: p.read_text() for p in src.rglob("*.py")} == before  # originals untouched
    assert (out / ".dagshift-upgrade.json").exists() and (out / "AIRFLOW_UPGRADE_REPORT.md").exists()
    classic = next(c for c in res["changed"] if c["relPath"] == "dags/classic_operators.py")
    assert classic["fixes"] > 0 and "airflow.providers.standard" in open(classic["upgraded"]).read()
    assert (out / "requirements.txt").exists()  # unchanged files are copied too
    # a second copy over the first needs replace=True; a foreign folder is refused
    again = server("writeCopy", root=str(src), outDir=str(out))["result"]
    assert again == {"ok": False, "reason": "earlier-copy", "message": again["message"], "outDir": str(out)}
    assert server("writeCopy", root=str(src), outDir=str(out), replace=True)["result"]["ok"]
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("mine")
    refused = server("writeCopy", root=str(src), outDir=str(foreign), replace=True)["result"]
    assert refused["reason"] == "not-empty" and (foreign / "keep.txt").read_text() == "mine"


def test_write_copy_inside_the_project_is_not_scanned_again(server, tmp_path):
    src = _copy_project(tmp_path)
    out = src / "upgraded"
    res = server("writeCopy", root=str(src), outDir=str(out))["result"]
    assert res["ok"] and not (out / "upgraded").exists()
    check = server("check", root=str(src))["result"]
    # compare path components, not a "/upgraded/" substring, which never matches a Windows path
    assert check["files"] and not any("upgraded" in Path(f["path"]).parts for f in check["files"])
    # the copy itself, opened on its own, is scanned (and has nothing left to fix automatically)
    own = server("check", root=str(out))["result"]
    assert own["files"] and not any(f["changed"] for f in own["files"])


def test_write_copy_refuses_to_overwrite_the_originals(server, tmp_path):
    src = _copy_project(tmp_path)
    err = server("writeCopy", root=str(src), outDir=str(tmp_path))["error"]
    assert "overwrite the originals" in err["message"]
