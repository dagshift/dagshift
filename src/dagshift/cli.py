"""dagshift command line.

    dagshift detect   PATH...            which Airflow version does this project run today?
    dagshift check    PATH...            report only; non-zero exit for CI gates
    dagshift migrate  PATH... --out DIR  write migrated copies (or --in-place)
    dagshift validate DAG_FOLDER --airflow-python PY
    dagshift serve                       editor protocol (used by the VS Code extension)
    dagshift rules                       list rules
    dagshift targets                     list Airflow targets and where their facts came from
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from . import __version__
from .engine import migrate_paths
from .findings import Severity
from .kb import available_targets, KnowledgeBase
from .options import find_config, load_options
from .plugins import load_plugins
from .report import to_json, to_markdown, to_sarif, to_text
from .rules_catalog import RULES


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("paths", nargs="+", help="files or directories (DAGs, plugins, configs, requirements, infra)")
    p.add_argument("--target", help=f"Airflow version to upgrade to ({', '.join(available_targets())}); default: newest")
    p.add_argument("--source", help="Airflow version the project runs today (e.g. 2.10.5); default: detect it")
    p.add_argument("--source-path", action="append", default=None,
                   help="extra file or folder to detect the current version from, e.g. the Terraform repo (repeatable)")
    p.add_argument("--config", help="dagshift.toml or pyproject.toml with [tool.dagshift] (auto-discovered)")
    p.add_argument("--select", help="comma-separated rule ids/prefixes to run, e.g. DS1,DS201")
    p.add_argument("--ignore", help="comma-separated rule ids/prefixes to skip")
    p.add_argument("--exclude", action="append", default=None, help="path glob to skip (repeatable)")
    p.add_argument("--start-date", help="YYYY-MM-DD used to turn days_ago(n) into a fixed date (default: today)")
    p.add_argument("--no-modernize", action="store_true", help="only rewrite imports that break or warn")
    p.add_argument("--no-preserve-schedule", action="store_true",
                   help="do not add schedule=timedelta(days=1) to DAGs that had no schedule")
    p.add_argument("--pin-catchup", action="store_true", help="add catchup=True where catchup was implicit")
    p.add_argument("--keep-cron-data-intervals", action="store_true",
                   help="wrap cron/timedelta schedules in *DataIntervalTimetable (keeps Airflow 2 ds/logical_date)")
    p.add_argument("--rename-config-keys", action="store_true",
                   help="rename Airflow parameter keys in non-dag-factory YAML/JSON")
    p.add_argument("--no-strings", action="store_true", help="do not touch Airflow code inside string literals")
    p.add_argument("--format", choices=["text", "md", "json", "sarif"], default="text")
    p.add_argument("--output", "-o", help="write the report here instead of stdout")
    p.add_argument("--verbose", "-v", action="store_true", help="include info findings and recipes")


def _options(args):
    config = Path(args.config) if args.config else find_config(Path(args.paths[0]))
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()] if s else None  # noqa: E731
    opts = load_options(
        config,
        target=args.target,
        source=args.source,
        source_paths=args.source_path,
        select=split(args.select),
        ignore=split(args.ignore),
        exclude=args.exclude,
        start_date=dt.date.fromisoformat(args.start_date) if args.start_date else None,
        modernize=False if args.no_modernize else None,
        preserve_schedule_default=False if args.no_preserve_schedule else None,
        pin_catchup=True if args.pin_catchup else None,
        keep_cron_data_intervals=True if args.keep_cron_data_intervals else None,
        rename_config_keys=True if args.rename_config_keys else None,
        migrate_strings=False if args.no_strings else None,
    )
    extra = load_plugins()
    if extra:
        opts = opts.merged(extra_moves={**extra, **opts.extra_moves})
    return opts, config


def _emit(text: str, output: str | None) -> None:
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def _render(run, fmt: str, verbose: bool) -> str:
    return {"text": lambda: to_text(run, verbose), "md": lambda: to_markdown(run),
            "json": lambda: to_json(run), "sarif": lambda: to_sarif(run)}[fmt]()


def cmd_check(args) -> int:
    opts, _ = _options(args)
    run = migrate_paths(args.paths, opts)
    _emit(_render(run, args.format, args.verbose), args.output)
    fail_on = args.fail_on
    if fail_on == "never":
        return 0
    if run.errors:
        return 2
    if fail_on == "manual":
        return 1 if run.count(Severity.MANUAL) else 0
    if fail_on == "warn":
        return 1 if run.count(Severity.MANUAL) + run.count(Severity.WARN) else 0
    return 1 if (run.changed or run.count(Severity.MANUAL)) else 0  # fail_on == "any"


def cmd_migrate(args) -> int:
    opts, _ = _options(args)
    run = migrate_paths(args.paths, opts)
    if args.in_place:
        written = run.write_in_place()
    elif args.out:
        written = run.write_to(Path(args.out))
    else:
        written = []
    if args.diff:
        Path(args.diff).write_text(run.diff(), encoding="utf-8")
    if args.report:
        Path(args.report).write_text(to_markdown(run), encoding="utf-8")
    if args.json:
        Path(args.json).write_text(to_json(run), encoding="utf-8")
    if args.sarif:
        Path(args.sarif).write_text(to_sarif(run), encoding="utf-8")
    _emit(_render(run, args.format, args.verbose), args.output)
    if not args.in_place and not args.out:
        sys.stderr.write("dry run: pass --out DIR or --in-place to write files\n")
    else:
        sys.stderr.write(f"wrote {len(written)} changed file(s)\n")
    return 2 if run.errors else 0


def cmd_validate(args) -> int:
    from .validate import validate

    res = validate(args.dag_folder, args.airflow_python, render_cmd=args.render_cmd,
                   pythonpath=args.pythonpath or None)
    if args.format == "json":
        import json

        _emit(json.dumps(res.__dict__, indent=2, default=str), args.output)
    else:
        _emit(res.to_text(), args.output)
    return 0 if res.ok else 1


def cmd_detect(args) -> int:
    from .versions import detect_version

    det = detect_version(args.paths, args.source_path or [])
    if args.format == "json":
        import json

        print(json.dumps(det.to_dict(), indent=2))
        return 0 if det.version else 1
    if det.version:
        print(f"Airflow {det.version} ({det.confidence})")
    else:
        print("Airflow version: unknown")
    if det.note:
        print(det.note)
    for e in det.evidence[:15]:
        print(f"  {e.version:<8} {e.kind:<12} {e.file}:{e.line}  {e.text}")
    return 0 if det.version else 1


def cmd_rules(args) -> int:
    for rid, (name, help_) in RULES.items():
        print(f"{rid}  {name:<26} {help_}")
    return 0


def cmd_targets(args) -> int:
    for t in available_targets():
        kb = KnowledgeBase(t)
        print(f"{t:<5} verified against Airflow {kb.built_from}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dagshift",
                                     description="Upgrade Airflow DAG code, plugins and configs between Airflow versions.")
    parser.add_argument("--version", action="version", version=f"dagshift {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("detect", help="detect the Airflow version a project runs today")
    p.add_argument("paths", nargs="+")
    p.add_argument("--source-path", action="append", default=None, help="extra file or folder to search (repeatable)")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("check", help="report what would change and what needs you")
    _add_common(p)
    p.add_argument("--fail-on", choices=["any", "manual", "warn", "never"], default="any",
                   help="exit 1 when: any change or manual item (default) / manual items / manual or warn / never")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("migrate", help="rewrite files")
    _add_common(p)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--out", help="write a migrated copy of every path into this directory")
    g.add_argument("--in-place", action="store_true", help="overwrite files in place")
    p.add_argument("--diff", help="also write a unified diff to this file")
    p.add_argument("--report", help="also write a Markdown report to this file")
    p.add_argument("--json", help="also write a JSON report to this file")
    p.add_argument("--sarif", help="also write a SARIF report to this file")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("validate", help="load DAGs with the target Airflow install")
    p.add_argument("dag_folder")
    p.add_argument("--airflow-python", required=True, help="python of a venv/image with the target Airflow")
    p.add_argument("--render-cmd", help="shell command that renders templated DAGs (run in a temp copy)")
    p.add_argument("--pythonpath", action="append", help="extra import roots for DAG files (repeatable)")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--output", "-o")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("serve", help="JSON protocol on stdin/stdout for editor integrations")
    p.set_defaults(func=lambda _args: __import__("dagshift.server", fromlist=["serve"]).serve())

    p = sub.add_parser("rules", help="list rules")
    p.set_defaults(func=cmd_rules)
    p = sub.add_parser("targets", help="list supported Airflow targets")
    p.set_defaults(func=cmd_targets)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
