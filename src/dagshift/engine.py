"""Walk paths, pick the right migrator per file, and collect results."""
from __future__ import annotations

import datetime as _dt
import difflib
import json
import os
from concurrent.futures import ProcessPoolExecutor
import re
import fnmatch
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .findings import BLOCKING, FileResult, Finding, Severity
from .jinja_mask import mask
from .jinja_text import LEGACY_KEYS
from .kb import KnowledgeBase
from .options import COPY_EXCLUDES, DEFAULT_EXCLUDES, OUTPUT_MARKER, Options
from .platform_rules import is_requirements, provider_package, providers_listed, scan_platform_file
from .python.migrator import migrate_python_source
from .text_migrator import migrate_config_text
from .versions import Detection, detect_version

_PLATFORM_SUFFIXES = (".tf", ".tfvars", ".hcl")


def classify(path: Path, text: str, options: Options) -> str | None:
    """python | yaml | json | text | platform | None (skip)."""
    name = path.name
    low = name.lower()
    if name == ".airflowignore" or name == "airflow.cfg" or is_requirements(name):
        return "platform"
    if low.startswith("dockerfile") or low.endswith(".dockerfile") or low.endswith(_PLATFORM_SUFFIXES):
        return "platform"
    suffix = path.suffix.lower()
    if suffix in options.template_extensions:
        inner = Path(path.stem).suffix.lower()  # dag.py.j2 -> .py
        if inner == ".py":
            return "python"
        if inner in (".yaml", ".yml"):
            return "yaml"
        if inner == ".json":
            return "json"
        try:
            import libcst as cst

            cst.parse_module(mask(text).code)
            if "airflow" in text:
                return "python"
        except Exception:
            pass
        return "text"
    if suffix == ".py":
        return "python"
    if suffix in (".yaml", ".yml"):
        return "yaml"
    if suffix == ".json":
        return "json"
    return None


@dataclass
class MigrationRun:
    options: Options
    kb: KnowledgeBase
    roots: list[Path]
    files: list[FileResult] = field(default_factory=list)
    project_findings: list[Finding] = field(default_factory=list)
    detection: Detection | None = None  # how the current version was found (None when given explicitly)

    # ---- summaries --------------------------------------------------------------------
    @property
    def findings(self) -> list[Finding]:
        out = [f for r in self.files for f in r.findings]
        return out + self.project_findings

    def count(self, severity: Severity) -> int:
        return sum(1 for f in self.findings if f.severity == severity)

    @property
    def changed(self) -> list[FileResult]:
        return [r for r in self.files if r.changed]

    @property
    def errors(self) -> list[FileResult]:
        return [r for r in self.files if r.error]

    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity in BLOCKING]

    def diff(self) -> str:
        chunks = []
        for r in self.changed:
            chunks.append("".join(difflib.unified_diff(
                r.original.splitlines(keepends=True), r.migrated.splitlines(keepends=True),
                fromfile=f"a/{r.path}", tofile=f"b/{r.path}",
            )))
        return "".join(c if c.endswith("\n") else c + "\n" for c in chunks)

    # ---- output -----------------------------------------------------------------------
    def write_in_place(self) -> list[Path]:
        written = []
        for r in self.changed:
            if r.source_path:
                Path(r.source_path).write_text(r.migrated, encoding="utf-8")
                written.append(Path(r.source_path))
        return written

    def write_to(self, out_dir: Path) -> list[Path]:
        """Copy every root into out_dir, then write migrated files over the copies.

        One directory root: out_dir mirrors it. Several roots: out_dir/<root name>/...
        out_dir may sit inside a root (it is not copied into itself); it may not be a root or contain one.
        A marker file records what the copy is, so later scans skip it and a later copy may replace it.
        """
        out_dir = Path(out_dir).resolve()
        for root in self.roots:
            r = root.resolve()
            if out_dir == r or (r.is_dir() and r.is_relative_to(out_dir)):
                raise ValueError(f"cannot write the upgraded copy to {out_dir}: it would overwrite {r}")
        single = len(self.roots) == 1 and self.roots[0].is_dir()

        def dest_root(root: Path) -> Path:
            return out_dir if single else out_dir / root.name

        base_ignore = shutil.ignore_patterns(*COPY_EXCLUDES)

        def ignore(src: str, names: list[str]) -> set[str]:
            skipped = set(base_ignore(src, names))
            for n in names:
                p = Path(src, n)
                if p.is_dir() and (p.resolve() == out_dir or (p / OUTPUT_MARKER).exists()):
                    skipped.add(n)  # the copy itself, or an earlier upgraded copy
            return skipped

        for root in self.roots:
            if root.is_dir():
                shutil.copytree(root, dest_root(root), dirs_exist_ok=True, ignore=ignore)
            else:
                out_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root, out_dir / root.name)
        written = []
        for r in self.changed:
            root = Path(r.root) if r.root else None
            if root is not None and root.is_dir():
                dest = dest_root(root) / r.path
            else:
                dest = out_dir / Path(r.path).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(r.migrated, encoding="utf-8")
            written.append(dest)
        from . import __version__

        (out_dir / OUTPUT_MARKER).write_text(json.dumps({
            "tool": "dagshift",
            "version": __version__,
            "source": self.kb.source,
            "target": self.kb.target,
            "from": [str(p.resolve()) for p in self.roots],
            "changed": sorted(r.path for r in self.changed),
            "written_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        }, indent=2) + "\n", encoding="utf-8")
        return written


def prepare_output(out_dir: Path, replace: bool = False) -> None:
    """Make sure out_dir can take an upgraded copy without destroying anything that dagshift did not write.

    Empty or missing: fine. Holds an earlier dagshift copy: removed when replace is set. Anything else: refused.
    """
    out_dir = Path(out_dir)
    if not out_dir.exists():
        return
    if not out_dir.is_dir():
        raise OutputFolderError(f"{out_dir} is a file, not a folder", "not-a-folder")
    if not any(out_dir.iterdir()):
        return
    if not (out_dir / OUTPUT_MARKER).exists():
        raise OutputFolderError(f"{out_dir} is not empty and was not written by dagshift; choose another folder",
                                "not-empty")
    if not replace:
        raise OutputFolderError(f"{out_dir} holds an earlier upgraded copy; pass replace to overwrite it",
                                "earlier-copy")
    shutil.rmtree(out_dir)


class OutputFolderError(ValueError):
    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _excluded(rel: str, options: Options) -> bool:
    parts = Path(rel).parts
    if any(p in DEFAULT_EXCLUDES for p in parts):
        return True
    return any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(Path(rel).name, pat) for pat in options.exclude)


def iter_files(roots: list[Path], options: Options):
    """Files under roots, in path order. Excluded folders and earlier upgraded copies are not entered."""
    for root in roots:
        if root.is_file():
            yield root, root
            continue
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            if here != root and OUTPUT_MARKER in filenames:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in DEFAULT_EXCLUDES]
            found.extend(here / name for name in filenames)
        for p in sorted(found):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if _excluded(rel, options):
                continue
            yield p, root


_ALWAYS_RELEVANT = ("airflow", "{{", "{%", "xcom_pull", "dag_run", "provide_context", "schedule_interval")


def is_relevant(text: str, kb: KnowledgeBase) -> bool:
    """Cheap pre-check: can any rule fire on this file? Skips libcst for unrelated Python files.

    Helper modules matter even without airflow imports (callables that read context["execution_date"]),
    so removed context keys count as relevant too.
    """
    from .python import migrator

    if migrator.EXTRA_PASSES:
        return True
    if any(tok in text for tok in _ALWAYS_RELEVANT):
        return True
    if any(k in text for k in LEGACY_KEYS if k != "conf"):
        return True
    return any(root in text for root in {k.split(".")[0] for k in kb.extra_moves})


def migrate_file_text(text: str, rel: str, kind: str, kb: KnowledgeBase, options: Options) -> FileResult:
    if kind == "python" and not is_relevant(text, kb):
        return FileResult(path=rel, original=text, migrated=text)
    if kind == "python":
        res = migrate_python_source(text, rel, kb, options)
        if res.error and Path(rel).suffix.lower() in options.template_extensions:
            # A template that is not parseable Python even after masking: text-level fixes only.
            note = res.error
            res = migrate_config_text(text, rel, kb, options, "text")
            res.findings.insert(0, Finding(
                "DS505", Severity.INFO, rel, 1,
                f"Template could not be parsed as Python ({note}); applied text-level fixes only "
                "(import paths, template variables).",
                "Render it and run `dagshift check` on the output for full coverage."))
    elif kind in ("yaml", "json", "text"):
        res = migrate_config_text(text, rel, kb, options, kind)
    else:
        res = FileResult(path=rel, original=text, migrated=text)
    name = Path(rel).name
    if kind == "platform" or name.endswith((".yaml", ".yml", ".json")) or is_requirements(name):
        res.findings.extend(f for f in scan_platform_file(rel, text, kb) if options.rule_enabled(f.rule_id))
    return res


def _work(job: tuple) -> FileResult:
    """One file. Top-level so it can run in a worker process."""
    path, root, rel, text, kind, options = job
    kb = KnowledgeBase(options.target, options.extra_moves, options.source)
    res = migrate_file_text(text, rel, kind, kb, options)
    res.kind, res.source_path, res.root = kind, str(Path(path).resolve()), str(root)
    if kind in ("python", "yaml", "json", "text"):
        res.providers = _providers_from(res)
    return res


def _init_worker() -> None:
    from .plugins import load_plugins

    load_plugins()


def resolve_source(paths: list[str | Path], options: Options) -> tuple[Options, Detection | None]:
    """Fill options.source from detection when it is "auto" or unset."""
    if options.source and str(options.source).lower() != "auto":
        return options, None
    detection = detect_version(paths, options.source_paths)
    return options.merged(source=detection.version or "auto"), detection


def migrate_paths(paths: list[str | Path], options: Options | None = None, jobs: int | None = None) -> MigrationRun:
    """Migrate every relevant file under paths (in memory; nothing is written).

    jobs: worker processes for large trees (default: up to 8, only when there are 16+ Python files).
    """
    options, detection = resolve_source(paths, options or Options())
    kb = KnowledgeBase(options.target, options.extra_moves, options.source)
    roots = [Path(p) for p in paths]
    run = MigrationRun(options=options, kb=kb, roots=roots, detection=detection)
    requirement_files: dict[str, str] = {}
    work: list[tuple] = []
    for path, root in iter_files(roots, options):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        kind = classify(path, text, options)
        if kind is None:
            continue
        rel = path.name if root.is_file() else path.relative_to(root).as_posix()
        if is_requirements(path.name):
            requirement_files[rel] = text
        work.append((str(path), str(root), rel, text, kind, options))

    heavy = sum(1 for w in work if w[4] == "python" and is_relevant(w[3], kb))
    if jobs is None:
        jobs = min(8, os.cpu_count() or 1) if heavy >= 16 else 1
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker) as pool:
            run.files.extend(pool.map(_work, work, chunksize=4))
    else:
        run.files.extend(_work(w) for w in work)
    _provider_requirements(run, requirement_files)
    return run


_IMPORT_LINE = re.compile(r"^\s*(?:from|import)\s+(airflow\.providers\.[\w.]+)", re.M)


def _providers_from(res: FileResult) -> set[str]:
    """Provider packages that the migrated file imports and the original did not."""
    def pkgs(text: str) -> set[str]:
        return {p for p in (provider_package(m) for m in _IMPORT_LINE.findall(text)) if p}

    return pkgs(res.migrated) - pkgs(res.original)


def _provider_requirements(run: MigrationRun, requirement_files: dict[str, str]) -> None:
    needed: set[str] = set()
    for r in run.files:
        needed |= r.providers
    if not needed or not run.options.rule_enabled("DS802"):
        return
    listed: set[str] = set()
    for text in requirement_files.values():
        listed |= providers_listed(text)
    missing = sorted(needed - listed)
    if not missing:
        return
    where = sorted(requirement_files)[0] if requirement_files else "(project)"
    run.project_findings.append(Finding(
        "DS802", Severity.INFO, where, 1,
        f"Migrated imports now use {', '.join(missing)}, which your requirements do not list.",
        "Add them (versions from the constraints file of your target Airflow release). Managed images "
        "often preinstall the standard provider; check the image's package list before pinning.",
    ))
