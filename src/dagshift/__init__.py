"""dagshift: deterministic Airflow version upgrades (2.x -> 2.y, 2.x -> 3.y, 3.x -> 3.y) for DAG code and
the files around it.

Python API::

    from dagshift import Options, migrate_source, migrate_paths

    res = migrate_source(code, filename="dags/etl.py", options=Options(target="3.1"))
    print(res.migrated)
    for f in res.findings:
        print(f.rule_id, f.severity.value, f.line, f.message)

    run = migrate_paths(["dags/", "configs/"], Options(target="3.3"))
    run.write_to("dags_af3/")          # or run.write_in_place()
    print(run.diff())
"""
from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"

from .engine import MigrationRun, classify, migrate_file_text, migrate_paths  # noqa: E402
from .findings import FileResult, Finding, Severity  # noqa: E402
from .kb import KnowledgeBase, available_targets  # noqa: E402
from .options import Options, find_config, load_options  # noqa: E402
from .validate import ValidationResult, validate  # noqa: E402
from .versions import Detection, detect_version  # noqa: E402


def migrate_source(source: str, filename: str = "dag.py", options: Options | None = None) -> FileResult:
    """Migrate one file's text. The filename picks the migrator (.py, .yaml, .json, templates)."""
    options = options or Options()
    kb = KnowledgeBase(options.target, options.extra_moves, options.source)
    kind = classify(Path(filename), source, options) or "python"
    res = migrate_file_text(source, filename, kind, kb, options)
    res.kind = kind
    return res


__all__ = [
    "FileResult", "Finding", "KnowledgeBase", "MigrationRun", "Options", "Severity", "ValidationResult",
    "Detection", "available_targets", "detect_version", "find_config", "load_options", "migrate_paths",
    "migrate_source", "validate",
    "__version__",
]
