"""Load DAG files in a real Airflow 3 environment and report what breaks.

Static rules cannot see everything (values injected by generators, kwargs built at run
time, third-party operators). `dagshift validate` closes that gap: it runs Airflow's own
DagBag in the interpreter you point it at and returns import errors plus every
deprecation warning raised while parsing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPT = r'''
import json, os, sys, warnings
out_path, dag_folder = sys.argv[1], sys.argv[2]
result = {"import_errors": {}, "dags": {}, "warnings": [], "airflow_version": None}
try:
    import airflow
    result["airflow_version"] = airflow.__version__
    try:
        from airflow.dag_processing.dagbag import DagBag
    except ImportError:
        from airflow.models.dagbag import DagBag
    import inspect
    params = inspect.signature(DagBag.__init__).parameters
    kwargs = {"dag_folder": dag_folder, "safe_mode": False}
    if "include_examples" in params:
        kwargs["include_examples"] = False
    if "bundle_path" in params:
        kwargs["bundle_path"] = __import__("pathlib").Path(dag_folder)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        bag = DagBag(**kwargs)
    def rel(p):
        p = str(p)
        return os.path.relpath(p, dag_folder) if os.path.isabs(p) else p
    result["import_errors"] = {rel(k): v for k, v in bag.import_errors.items()}
    for dag_id, dag in bag.dags.items():
        result["dags"][dag_id] = {
            "file": rel(dag.fileloc),
            "tasks": len(dag.tasks),
            "timetable": type(dag.timetable).__name__,
        }
    seen = set()
    for w in caught:
        fn = getattr(w, "filename", "") or ""
        if not fn.startswith(os.path.abspath(dag_folder)):
            continue
        key = (os.path.relpath(fn, dag_folder), w.lineno, str(w.message))
        if key in seen:
            continue
        seen.add(key)
        result["warnings"].append({"file": key[0], "line": key[1], "category": w.category.__name__,
                                   "message": str(w.message)})
except Exception as exc:
    result["fatal"] = f"{type(exc).__name__}: {exc}"
with open(out_path, "w") as fh:
    json.dump(result, fh, indent=2, default=str)
'''


@dataclass
class ValidationResult:
    airflow_version: str | None
    import_errors: dict[str, str] = field(default_factory=dict)
    dags: dict[str, dict] = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)
    fatal: str | None = None

    @property
    def ok(self) -> bool:
        return not self.import_errors and not self.fatal

    def to_text(self) -> str:
        lines = [f"Airflow {self.airflow_version}: {len(self.dags)} DAG(s) loaded, "
                 f"{len(self.import_errors)} import error(s), {len(self.warnings)} warning(s)"]
        if self.fatal:
            lines.append(f"FATAL: {self.fatal}")
        for path, err in sorted(self.import_errors.items()):
            last = err.strip().splitlines()[-1] if err.strip() else err
            lines.append(f"  ERROR {path}: {last}")
            if "SUPERVISOR_COMMS" in err:
                lines.append("        (top-level Variable/Connection access needs a running DAG processor; this check "
                             "parses in-process. Confirm with `airflow dags reserialize` in a real environment, and "
                             "consider moving the lookup into a template or the task.)")
        for w in self.warnings:
            lines.append(f"  WARN  {w['file']}:{w['line']} {w['category']}: {w['message'][:200]}")
        for dag_id, d in sorted(self.dags.items()):
            lines.append(f"  ok    {dag_id} ({d['file']}, {d['tasks']} tasks, {d['timetable']})")
        return "\n".join(lines) + "\n"


def validate(
    dag_folder: str | Path,
    airflow_python: str,
    render_cmd: str | None = None,
    pythonpath: list[str] | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 600,
) -> ValidationResult:
    """Parse dag_folder with the Airflow installed for `airflow_python`.

    render_cmd: optional shell command run first in a temporary copy of the folder's parent
    (for DAG factories that render templates at build time). The copy is what gets parsed,
    so your working tree is never touched.
    """
    folder = Path(dag_folder).resolve()
    with tempfile.TemporaryDirectory(prefix="dagshift-validate-") as tmp:
        work = Path(tmp) / "work"
        target = folder
        if render_cmd:
            shutil.copytree(folder.parent, work, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"))
            proc = subprocess.run(render_cmd, shell=True, cwd=work, capture_output=True, text=True, timeout=timeout)
            if proc.returncode != 0:
                return ValidationResult(None, fatal=f"render command failed ({proc.returncode}): "
                                                    f"{(proc.stderr or proc.stdout)[-2000:]}")
            target = work / folder.name
        home = Path(tmp) / "airflow_home"
        home.mkdir()
        script = Path(tmp) / "dagshift_validate.py"
        script.write_text(_SCRIPT, encoding="utf-8")
        out = Path(tmp) / "result.json"
        child_env = dict(os.environ)
        child_env.update({
            "AIRFLOW_HOME": str(home),
            "AIRFLOW__CORE__LOAD_EXAMPLES": "False",
            "AIRFLOW__CORE__DAGS_FOLDER": str(target),
        })
        paths = [str(p) for p in (pythonpath or [])]
        if render_cmd:
            paths = [str(work / Path(p).relative_to(folder.parent)) if Path(p).resolve().is_relative_to(folder.parent)
                     else p for p in paths]
        if paths:
            child_env["PYTHONPATH"] = os.pathsep.join(paths + [child_env.get("PYTHONPATH", "")]).strip(os.pathsep)
        if env:
            child_env.update(env)
        proc = subprocess.run([airflow_python, str(script), str(out), str(target)], env=child_env,
                              capture_output=True, text=True, timeout=timeout)
        if not out.exists():
            return ValidationResult(None, fatal=f"validator crashed: {proc.stderr[-2000:]}")
        data = json.loads(out.read_text())
        return ValidationResult(
            airflow_version=data.get("airflow_version"),
            import_errors=data.get("import_errors", {}),
            dags=data.get("dags", {}),
            warnings=data.get("warnings", []),
            fatal=data.get("fatal"),
        )
