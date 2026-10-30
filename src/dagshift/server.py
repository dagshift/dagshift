"""Editor protocol: line-delimited JSON over stdio (`dagshift serve`).

One request per line on stdin, one response per line on stdout:

    -> {"id": 1, "method": "check", "params": {"root": "/abs/workspace"}}
    <- {"id": 1, "result": {...}}            or  {"id": 1, "error": {"message": "...", "type": "..."}}

Methods
    initialize       -> {version, targets, sources, defaultTarget, rules}
    detect           root, extraPaths?                -> the Airflow version the project runs today, with evidence
    check            root, paths?, options?          -> findings for every relevant file (no file bodies)
    migrateDocument  path, text, root?, options?     -> one file, using unsaved editor text
    migrateFiles     root, files: [{path, text?}], options? -> migrated bodies for the given files
    report           root, paths?, options?, format  -> {text} (md | json | sarif | text)
    validate         dagFolder, airflowPython, renderCmd?, pythonpath? -> parse DAGs on a real Airflow 3
    shutdown         -> null, then the process exits

The server never writes to the user's files. Editors apply the returned text themselves, so every
change goes through their own undo, preview and save machinery.

Options are camelCase or snake_case Options fields. They are applied on top of the project's
dagshift.toml / [tool.dagshift] found from `root`.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import fields
from pathlib import Path

from . import __version__
from .engine import classify, migrate_file_text, migrate_paths, prepare_output, OutputFolderError
from .findings import FileResult, Finding
from .kb import DEFAULT_TARGET, KnowledgeBase, available_targets
from .engine import resolve_source
from .options import Options, _coerce, find_config, load_options
from .plugins import load_plugins
from .report import summary_counts, to_json, to_markdown, to_sarif, to_text
from .rules_catalog import RULES
from .versions import KNOWN_SOURCES, detect_version

_FIELDS = {f.name for f in fields(Options)}


def _snake(name: str) -> str:
    out = []
    for ch in name:
        if ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def build_options(root: str | None, overrides: dict | None) -> Options:
    config = find_config(Path(root)) if root else None
    clean = {}
    for k, v in (overrides or {}).items():
        key = _snake(k)
        if key not in _FIELDS:
            raise ValueError(f"unknown option {k!r}")
        if v is None:
            continue
        clean[key] = _coerce(key, v)
    opts = load_options(config, **clean)
    extra = load_plugins()
    if extra:
        opts = opts.merged(extra_moves={**extra, **opts.extra_moves})
    return opts


def _finding(f: Finding, root: str | None, file_abs: str | None = None) -> dict:
    d = f.to_dict()
    if file_abs:
        d["absPath"] = file_abs
    elif root and f.path and not f.path.startswith("("):
        d["absPath"] = str((Path(root) / f.path).resolve())
    else:
        d["absPath"] = None
    return d


def _file(res: FileResult, root: str | None, include_migrated: bool) -> dict:
    abs_path = res.source_path or (str((Path(root) / res.path).resolve()) if root else res.path)
    out = {
        "path": abs_path,
        "relPath": res.path,
        "kind": res.kind,
        "changed": res.changed,
        "templated": res.templated,
        "error": res.error,
        "findings": [_finding(f, root, abs_path) for f in res.findings],
    }
    if include_migrated:
        out["migrated"] = res.migrated if res.changed else None
    return out


class Server:
    def __init__(self) -> None:
        self.running = True
        self._detected: dict[tuple, str] = {}  # (root, source_paths) -> detected source, per session

    # -- methods -------------------------------------------------------------------------
    def initialize(self, params: dict) -> dict:
        targets = available_targets()
        return {
            "version": __version__,
            "targets": [{"target": t, "verifiedAgainst": KnowledgeBase(t).built_from} for t in targets],
            "sources": KNOWN_SOURCES,
            "defaultTarget": DEFAULT_TARGET,
            "rules": {rid: {"name": n, "help": h} for rid, (n, h) in RULES.items()},
        }

    def detect(self, params: dict) -> dict:
        root = params.get("root")
        paths = params.get("paths") or ([root] if root else [])
        return detect_version(paths, params.get("extraPaths") or []).to_dict()

    def check(self, params: dict) -> dict:
        root = params.get("root")
        paths = params.get("paths") or ([root] if root else [])
        if not paths:
            raise ValueError("check needs root or paths")
        opts = build_options(root, params.get("options"))
        self._detected.clear()  # a full scan re-detects
        run = migrate_paths(paths, opts)
        include = bool(params.get("includeMigrated"))
        files = [_file(r, r.root if r.root and Path(r.root).is_dir() else root, include)
                 for r in run.files if r.findings or r.changed or r.error]
        return {
            "target": run.kb.target,
            "source": run.kb.source,
            "detection": run.detection.to_dict() if run.detection else None,
            "verifiedAgainst": run.kb.built_from,
            "summary": summary_counts(run),
            "files": files,
            "projectFindings": [_finding(f, root) for f in run.project_findings],
        }

    def migrateDocument(self, params: dict) -> dict:  # noqa: N802 (protocol name)
        path = params["path"]
        text = params.get("text")
        if text is None:
            text = Path(path).read_text(encoding="utf-8")
        root = params.get("root")
        opts = build_options(root or str(Path(path).parent), params.get("options"))
        if not opts.source or str(opts.source).lower() == "auto":
            key = (root or str(Path(path).parent), tuple(opts.source_paths))
            if key not in self._detected:
                self._detected[key] = resolve_source([key[0]], opts)[0].source
            opts = opts.merged(source=self._detected[key])
        kind = classify(Path(path), text, opts)
        if kind is None:
            return {"path": path, "kind": None, "changed": False, "templated": False, "error": None,
                    "findings": [], "migrated": None}
        kb = KnowledgeBase(opts.target, opts.extra_moves, opts.source)
        rel = os.path.relpath(path, root) if root and Path(path).is_relative_to(root) else Path(path).name
        res = migrate_file_text(text, rel, kind, kb, opts)
        res.kind, res.source_path = kind, path
        return _file(res, root, include_migrated=True)

    def migrateFiles(self, params: dict) -> dict:  # noqa: N802
        root = params.get("root")
        out = []
        for item in params.get("files", []):
            out.append(self.migrateDocument({"path": item["path"], "text": item.get("text"), "root": root,
                                             "options": params.get("options")}))
        return {"files": out}

    def writeCopy(self, params: dict) -> dict:  # noqa: N802
        """Upgrade `path` (default: root) into a separate folder; the originals are not touched."""
        root = params.get("root")
        src = Path(params.get("path") or root).resolve()
        out = Path(params["outDir"]).expanduser().resolve()
        if out == src or src.is_relative_to(out):
            raise ValueError(f"the upgraded copy cannot go to {out}: that would overwrite the originals")
        opts = build_options(root, params.get("options"))
        try:
            prepare_output(out, bool(params.get("replace")))
        except OutputFolderError as exc:
            return {"ok": False, "reason": exc.reason, "message": str(exc), "outDir": str(out)}
        if out.is_relative_to(src):  # a copy inside the project is not scanned
            rel = out.relative_to(src).as_posix()
            opts = opts.merged(exclude=[*opts.exclude, rel, f"{rel}/*"])
        run = migrate_paths([src], opts)
        run.write_to(out)
        report = None
        if params.get("writeReport", True):
            report = out / "AIRFLOW_UPGRADE_REPORT.md"
            report.write_text(to_markdown(run), encoding="utf-8")
        changed = []
        for r in run.changed:
            changed.append({
                "relPath": r.path,
                "original": str(src / r.path),
                "upgraded": str(out / r.path),
                "fixes": sum(1 for f in r.findings if f.severity.value == "fixed"),
                "manual": sum(1 for f in r.findings if f.severity.value == "manual"),
            })
        return {
            "ok": True,
            "outDir": str(out),
            "source": run.kb.source,
            "target": run.kb.target,
            "summary": summary_counts(run),
            "changed": changed,
            "report": str(report) if report else None,
        }

    def report(self, params: dict) -> dict:
        root = params.get("root")
        paths = params.get("paths") or [root]
        opts = build_options(root, params.get("options"))
        run = migrate_paths(paths, opts)
        fmt = params.get("format", "md")
        render = {"md": to_markdown, "json": to_json, "sarif": to_sarif, "text": to_text}[fmt]
        return {"text": render(run), "summary": summary_counts(run)}

    def validate(self, params: dict) -> dict:
        from .validate import validate

        res = validate(params["dagFolder"], params["airflowPython"], render_cmd=params.get("renderCmd"),
                       pythonpath=params.get("pythonpath") or None)
        return {
            "ok": res.ok,
            "airflowVersion": res.airflow_version,
            "importErrors": res.import_errors,
            "dags": res.dags,
            "warnings": res.warnings,
            "fatal": res.fatal,
            "text": res.to_text(),
        }

    def shutdown(self, params: dict) -> None:
        self.running = False
        return None

    # -- loop ----------------------------------------------------------------------------
    def handle(self, line: str) -> dict | None:
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            return {"id": None, "error": {"type": "ParseError", "message": str(exc)}}
        rid = req.get("id")
        method = req.get("method", "")
        fn = getattr(self, method, None) if not method.startswith("_") else None
        if fn is None or method in ("handle", "serve"):
            return {"id": rid, "error": {"type": "MethodNotFound", "message": f"unknown method {method!r}"}}
        try:
            return {"id": rid, "result": fn(req.get("params") or {})}
        except Exception as exc:  # report, never crash the server
            return {"id": rid, "error": {"type": type(exc).__name__, "message": str(exc),
                                         "trace": traceback.format_exc(limit=8)}}


def serve(stdin=None, stdout=None) -> int:
    """Run the protocol loop. Anything the engine or plugins print goes to stderr."""
    if stdout is None:
        stdout = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n")
        sys.stdout = sys.stderr
    stdin = stdin or sys.stdin
    if hasattr(stdin, "reconfigure"):
        stdin.reconfigure(encoding="utf-8")
    server = Server()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        resp = server.handle(line)
        if resp is not None:
            stdout.write(json.dumps(resp, separators=(",", ":")) + "\n")
            stdout.flush()
        if not server.running:
            break
    return 0
