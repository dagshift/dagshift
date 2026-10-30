"""Harvest Airflow 2's own deprecation tables and verify their provider targets.

Airflow 2.x kept hundreds of legacy paths alive through shim tables
(airflow.contrib.*, airflow.hooks.postgres_hook, airflow.operators.mysql_operator, ...),
all of which are gone in Airflow 3. This script:

1. reads every add_deprecated_classes(...) table in an Airflow 2 wheel (AST only),
2. downloads the latest release of each provider those tables point to (no install),
3. checks the target class still exists in that provider release (AST only),
4. writes tools/af2_moves.json for build_kb.py.

Usage:
    python tools/harvest_af2.py /path/to/extracted/apache_airflow-2.11.x/airflow  WORKDIR
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from probe_airflow import harvest_deprecation_tables  # noqa: E402

# Provider ids whose package name has two segments.
TWO_SEGMENT = {"apache", "cncf", "common", "microsoft", "atlassian", "dbt", "fab"}


def provider_package(path: str) -> tuple[str, str] | None:
    parts = path.split(".")
    if len(parts) < 4 or parts[:2] != ["airflow", "providers"]:
        return None
    if parts[2] in TWO_SEGMENT:
        pid = f"{parts[2]}.{parts[3]}"
    else:
        pid = parts[2]
    return pid, "apache-airflow-providers-" + pid.replace(".", "-").replace("_", "-")


def defined_names(src: str) -> set[str]:
    names: set[str] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return names
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.If):  # TYPE_CHECKING / version branches
            for sub in node.body + node.orelse:
                if isinstance(sub, (ast.ClassDef, ast.FunctionDef)):
                    names.add(sub.name)
    return names


def main(af2_root: str, workdir: str) -> None:
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    tables = harvest_deprecation_tables(af2_root)
    tables = {k: v for k, v in tables.items() if not k.endswith(".*")}
    packages: dict[str, str] = {}
    for target in tables.values():
        pp = provider_package(target)
        if pp:
            packages[pp[1]] = pp[0]
    wheels: dict[str, zipfile.ZipFile] = {}
    versions: dict[str, str] = {}
    for pkg in sorted(packages):
        dest = work / pkg
        if not any(dest.glob("*.whl")):
            subprocess.run([sys.executable, "-m", "pip", "download", "--quiet", "--no-deps", "--only-binary=:all:",
                            "--python-version", "3.12", pkg, "-d", str(dest)], check=False)
        found = list(dest.glob("*.whl"))
        if found:
            wheels[pkg] = zipfile.ZipFile(found[0])
            versions[pkg] = found[0].name.split("-")[1]
    out: dict[str, dict] = {}
    for old, target in sorted(tables.items()):
        entry: dict = {"af2_target": target}
        pp = provider_package(target)
        if pp is None:
            entry["kind"] = "core"
            out[old] = entry
            continue
        pkg = pp[1]
        entry.update(kind="provider", package=pkg, version=versions.get(pkg))
        zf = wheels.get(pkg)
        if zf is None:
            entry["exists"] = None
            entry["note"] = f"{pkg} is no longer published; there is no maintained replacement path."
            out[old] = entry
            continue
        module, _, name = target.rpartition(".")
        rel = module.replace(".", "/")
        src = None
        for cand in (f"{rel}.py", f"{rel}/__init__.py"):
            try:
                src = zf.read(cand).decode("utf-8")
                break
            except KeyError:
                continue
        entry["exists"] = bool(src is not None and name in defined_names(src))
        if not entry["exists"]:
            entry["note"] = (f"Airflow 2 pointed this at {target}, which no longer exists in {pkg} "
                             f"{versions.get(pkg)}. Check the provider changelog for its replacement.")
        out[old] = entry
    (HERE / "af2_moves.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    ok = sum(1 for e in out.values() if e.get("exists"))
    print(f"{len(out)} legacy paths; {ok} provider targets verified; {len(versions)} provider wheels")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
