"""Version helpers and detection of the Airflow version a project runs today.

Detection looks for pinned versions first (requirements, constraints URLs, Docker images, compose,
Helm values, Cloud Composer image versions, MWAA, Astro Runtime) and falls back to features the
code uses. Enterprise setups often keep the Composer/MWAA definition in a separate infrastructure
repository: pass that path as an extra root, or set the version explicitly.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Versions offered as "current version" choices (sources can be older than the oldest target).
KNOWN_SOURCES = ["1.10", "2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.10", "2.11",
                 "3.0", "3.1", "3.2", "3.3"]


def vtuple(version: str | None) -> tuple[int, int]:
    """'2.10.5' -> (2, 10). Unknown / unparsable -> (0, 0)."""
    if not version:
        return (0, 0)
    m = re.match(r"\s*v?(\d+)(?:\.(\d+))?", str(version))
    if not m:
        return (0, 0)
    return int(m.group(1)), int(m.group(2) or 0)


def minor(version: str | None) -> str | None:
    if not version:
        return None
    major, mnr = vtuple(version)
    return f"{major}.{mnr}" if major else None


@dataclass
class Evidence:
    version: str
    file: str
    line: int
    kind: str  # requirements | constraints | docker | compose | helm | composer | mwaa | astro | code
    text: str


@dataclass
class Detection:
    version: str | None  # best guess, as precise as the evidence allows ("2.10.5" or "2.10")
    confidence: str  # pinned | inferred | unknown
    evidence: list[Evidence] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)  # other pinned versions found
    note: str = ""

    @property
    def minor(self) -> str | None:
        return minor(self.version)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["minor"] = self.minor
        return d


_PIN_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("requirements", re.compile(r"apache-airflow(?:\[[^\]]*\])?\s*(?:==|~=|===)\s*v?(\d+\.\d+(?:\.\d+)?)", re.I), "*"),
    ("requirements", re.compile(r"apache-airflow(?:\[[^\]]*\])?\s*>=\s*v?(\d+\.\d+(?:\.\d+)?)\s*,\s*<", re.I), "*"),
    ("constraints", re.compile(r"constraints-(\d+\.\d+\.\d+)/constraints-"), "*"),
    ("docker", re.compile(r"^\s*FROM\s+\S*apache/airflow:(?:slim-)?v?(\d+\.\d+\.\d+)", re.I | re.M), "docker"),
    ("compose", re.compile(r"apache/airflow:(?:slim-)?v?(\d+\.\d+\.\d+)"), "compose"),
    ("helm", re.compile(r"^\s*(?:airflowVersion|defaultAirflowTag)\s*:\s*['\"]?(\d+\.\d+\.\d+)", re.M), "yaml"),
    ("composer", re.compile(r"composer-\d+(?:\.\d+\.\d+)?-airflow-(\d+\.\d+\.\d+)"), "*"),
    ("composer", re.compile(r"composer-\d+-airflow-(\d+(?:\.\d+)?)(?![\d.-])"), "*"),
    ("mwaa", re.compile(r"airflow_version\s*=\s*\"(\d+\.\d+\.\d+)\""), "tf"),
    ("mwaa", re.compile(r"AirflowVersion\s*:\s*['\"]?(\d+\.\d+\.\d+)"), "yaml"),
]
_ASTRO = re.compile(r"astro-runtime:(\d+)(?:\.(\d+))?(?:-(\d+))?")
# Astro Runtime major -> Airflow minor (Runtime 3.x tracks Airflow 3.x directly).
_ASTRO_MAP = {9: "2.7", 10: "2.8", 11: "2.9", 12: "2.10", 13: "2.11"}

# Which evidence to show first: the deployed environment beats local tooling.
_KIND_ORDER = {"composer": 0, "mwaa": 1, "astro": 2, "docker": 3, "helm": 4, "compose": 5, "constraints": 6,
               "requirements": 7, "code": 9}

_SCAN_NAMES = re.compile(
    r"^(requirements.*\.(txt|in)|constraints.*\.txt|pyproject\.toml|setup\.py|setup\.cfg|Pipfile|Pipfile\.lock|"
    r"poetry\.lock|uv\.lock|environment\.ya?ml|Dockerfile.*|.*\.dockerfile|(docker-)?compose.*\.ya?ml|values.*\.ya?ml|"
    r".*\.tf|.*\.tfvars|.*\.hcl|.*\.ya?ml|.*\.json|cloudbuild.*|\.gitlab-ci\.yml)$",
    re.I,
)
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "site-packages", ".terraform", ".tox", "dist",
              "build"}

# Features in DAG code and the first Airflow version that has them (newest wins).
_FEATURES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"^\s*(from|import)\s+airflow\.sdk\b", re.M), "3.0", "imports airflow.sdk"),
    (re.compile(r"\bDeadlineAlert\b"), "3.1", "uses DeadlineAlert"),
    (re.compile(r"\bDatasetAlias\b"), "2.10", "uses DatasetAlias"),
    (re.compile(r"@task\.(bash|run_if|skip_if)\b"), "2.9", "uses @task.bash / run_if / skip_if"),
    (re.compile(r"@(setup|teardown)\b|\.as_(setup|teardown)\s*\("), "2.7", "uses setup/teardown tasks"),
    (re.compile(r"^\s*from\s+airflow\.datasets\s+import", re.M), "2.4", "uses Datasets"),
    (re.compile(r"\bEmptyOperator\b"), "2.3", "uses EmptyOperator"),
    (re.compile(r"\.expand(_kwargs)?\s*\("), "2.3", "uses dynamic task mapping"),
    (re.compile(r"\bdata_interval_(start|end)\b|\blogical_date\b"), "2.2", "uses data intervals / logical_date"),
    (re.compile(r"^\s*from\s+airflow\.decorators\s+import", re.M), "2.0", "uses TaskFlow decorators"),
]
_LEGACY = [
    (re.compile(r"^\s*from\s+airflow\.contrib\b", re.M), "1.10", "imports airflow.contrib"),
]


def _iter_files(root: Path, limit: int = 20000):
    if root.is_file():
        yield root
        return
    count = 0
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.is_dir():
                if p.name not in _SKIP_DIRS:
                    stack.append(p)
            elif p.is_file():
                count += 1
                if count > limit:
                    return
                yield p


def _rel(p: Path, root: Path) -> str:
    try:
        return p.relative_to(root if root.is_dir() else root.parent).as_posix()
    except ValueError:
        return str(p)


def _kind_ok(scope: str, name: str) -> bool:
    low = name.lower()
    if scope == "*":
        return True
    if scope == "docker":
        return low.startswith("dockerfile") or low.endswith(".dockerfile")
    if scope == "compose":
        return bool(re.match(r"(docker-)?compose.*\.ya?ml$", low))
    if scope == "yaml":
        return low.endswith((".yaml", ".yml"))
    if scope == "tf":
        return low.endswith((".tf", ".tfvars", ".hcl"))
    return False


def detect_version(paths: list[str | Path], extra: list[str | Path] | None = None) -> Detection:
    """Best guess of the Airflow version the project runs today, with the evidence behind it."""
    pins: list[Evidence] = []
    code_hits: list[Evidence] = []
    roots = [(Path(p), False) for p in paths] + [(Path(p), True) for p in (extra or [])]
    for root, external in roots:
        if not root.exists():
            continue
        # files from another repository are shown with that repository's folder name in front
        prefix = f"{(root if root.is_dir() else root.parent).name}/" if external else ""
        for f in _iter_files(root):
            name = f.name
            is_py = name.endswith(".py")
            if not is_py and not _SCAN_NAMES.match(name):
                continue
            try:
                if f.stat().st_size > 2_000_000:
                    continue
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = prefix + _rel(f, root)
            if not is_py:
                for kind, rx, scope in _PIN_PATTERNS:
                    if not _kind_ok(scope, name):
                        continue
                    for m in rx.finditer(text):
                        line = text.count("\n", 0, m.start()) + 1
                        pins.append(Evidence(m.group(1), rel, line, kind, m.group(0).strip()[:120]))
                for m in _ASTRO.finditer(text):
                    major = int(m.group(1))
                    version = f"{major}.{m.group(2) or 0}" if major == 3 else _ASTRO_MAP.get(major)
                    if version:
                        line = text.count("\n", 0, m.start()) + 1
                        pins.append(Evidence(version, rel, line, "astro", m.group(0)))
            elif "airflow" in text:
                for rx, version, why in _FEATURES + _LEGACY:
                    m = rx.search(text)
                    if m:
                        line = text.count("\n", 0, m.start()) + 1
                        code_hits.append(Evidence(version, rel, line, "code", why))

    # de-duplicate identical pins (same version in the same file)
    seen = set()
    unique: list[Evidence] = []
    for e in pins:
        key = (e.version, e.file, e.kind)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    pins = unique

    if pins:
        # Most common minor wins; within it, the most precise / highest patch.
        minors = Counter(minor(e.version) for e in pins)
        top_count = max(minors.values())
        best_minor = max((m for m, c in minors.items() if c == top_count), key=vtuple)
        in_minor = [e for e in pins if minor(e.version) == best_minor]
        best = max(in_minor, key=lambda e: (e.version.count("."), [int(x) for x in e.version.split(".")]))
        conflicts = sorted({minor(e.version) for e in pins if minor(e.version) != best_minor}, key=vtuple)
        ordered = sorted(pins, key=lambda e: (minor(e.version) != best_minor, _KIND_ORDER.get(e.kind, 8), e.file, e.line))
        note = ""
        if conflicts:
            note = (f"Found Airflow {best_minor} in {top_count} place(s) but also {', '.join(conflicts)}; "
                    "pick the version your environment actually runs.")
        return Detection(best.version, "pinned", ordered, conflicts, note)

    if code_hits:
        if any(e.version == "1.10" for e in code_hits) and not any(vtuple(e.version) >= (2, 0) for e in code_hits):
            return Detection("1.10", "inferred", code_hits[:10], [],
                             "No pinned version found; the code imports airflow.contrib (Airflow 1.10 style).")
        newest = max((e for e in code_hits if e.version != "1.10"), key=lambda e: vtuple(e.version), default=None)
        if newest is not None:
            hits = sorted(code_hits, key=lambda e: vtuple(e.version), reverse=True)[:10]
            return Detection(newest.version, "inferred", hits, [],
                             f"No pinned version found; the code needs at least Airflow {newest.version} "
                             f"({newest.text}). Set the current version if your environment is newer.")
    return Detection(None, "unknown", [], [],
                     "No Airflow version found in these files. If your Composer/MWAA definition lives in another "
                     "repository, point dagshift at it or set the current version.")
