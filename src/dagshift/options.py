"""Migration options and project config ([tool.dagshift] in pyproject.toml or dagshift.toml)."""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field, fields
from pathlib import Path

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ModuleNotFoundError:
        _toml = None  # type: ignore[assignment]

DEFAULT_TEMPLATE_EXTENSIONS = (".j2", ".jinja", ".jinja2", ".tmpl", ".tpl", ".template")
# Directories never scanned. ("env" is deliberately absent: config folders are often called env/.)
DEFAULT_EXCLUDES = (
    ".git", ".venv", "venv", "__pycache__", "node_modules", ".tox", ".mypy_cache",
    ".pytest_cache", "build", "dist", "site-packages", ".terraform",
)
# Written into every upgraded copy. Scans skip sub-folders that contain it, so a copy inside the project
# is never scanned twice, and only a folder that has it may be replaced by a later copy.
OUTPUT_MARKER = ".dagshift-upgrade.json"

# Directories not copied by `migrate --out`.
COPY_EXCLUDES = (".git", ".venv", "venv", "__pycache__", "node_modules", ".tox", ".mypy_cache",
                 ".pytest_cache", ".terraform")


@dataclass
class Options:
    """Every behaviour switch. Defaults preserve Airflow 2 semantics wherever a rewrite can.

    target                    Airflow minor to upgrade to: "2.7" .. "3.3" (default: newest, "3.3")
    source                    Airflow version the project runs today ("2.10.5", "2.10"), or "auto" / None to
                              detect it from requirements, images, Composer/MWAA definitions and the code
    source_paths              extra files or folders to detect the current version from (for example the
                              Terraform repository that defines the Composer environment)
    start_date                date used to turn days_ago(n) into a fixed start_date
                              (default: today); the result is start_date - n days
    modernize                 also move imports that still work but have an airflow.sdk home
    preserve_schedule_default add schedule=timedelta(days=1) to DAGs that had no schedule
                              (Airflow 2 ran them daily; Airflow 3 would never schedule them)
    pin_catchup               add catchup=True to DAGs without an explicit catchup
                              (Airflow 2 default was True; Airflow 3 default is False)
    keep_cron_data_intervals  wrap cron schedules in CronDataIntervalTimetable so logical_date,
                              ds and data_interval_* keep their Airflow 2 meaning
    rename_config_keys        rename Airflow parameter keys in YAML/JSON that is not dag-factory
    migrate_strings           migrate Airflow code embedded in string literals (code generators)
    select / ignore           rule ids or prefixes to include / exclude ("DS1", "DS503")
    exclude                   path globs to skip
    extra_moves               custom import moves {"old.path.Name": "new.path.Name"}
    template_extensions       file suffixes treated as templates
    """

    target: str = "3.3"
    source: str | None = None
    source_paths: list[str] = field(default_factory=list)
    start_date: _dt.date | None = None
    modernize: bool = True
    preserve_schedule_default: bool = True
    pin_catchup: bool = False
    keep_cron_data_intervals: bool = False
    rename_config_keys: bool = False
    migrate_strings: bool = True
    select: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    extra_moves: dict[str, str] = field(default_factory=dict)
    template_extensions: tuple[str, ...] = DEFAULT_TEMPLATE_EXTENSIONS

    def __post_init__(self) -> None:
        # the Python API accepts "YYYY-MM-DD" and datetimes as well as dates
        if isinstance(self.start_date, str):
            self.start_date = _dt.date.fromisoformat(self.start_date)
        elif isinstance(self.start_date, _dt.datetime):
            self.start_date = self.start_date.date()

    def anchor_date(self) -> _dt.date:
        return self.start_date or _dt.datetime.now(_dt.timezone.utc).date()

    def rule_enabled(self, rule_id: str) -> bool:
        if self.select and not any(rule_id.startswith(s) for s in self.select):
            return False
        return not any(rule_id.startswith(s) for s in self.ignore)

    def merged(self, **overrides) -> "Options":
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        for k, v in overrides.items():
            if v is not None:
                data[k] = v
        return Options(**data)


def _coerce(name: str, value):
    if name == "start_date" and isinstance(value, str):
        return _dt.date.fromisoformat(value)
    if name == "template_extensions" and isinstance(value, list):
        return tuple(value)
    return value


def find_config(start: Path) -> Path | None:
    start = start.resolve()
    for d in [start, *start.parents] if start.is_dir() else [start.parent, *start.parent.parents]:
        for name in ("dagshift.toml", "pyproject.toml"):
            p = d / name
            if p.is_file():
                if name == "pyproject.toml":
                    if _toml is None:
                        continue
                    data = _toml.loads(p.read_text(encoding="utf-8"))
                    if "dagshift" not in data.get("tool", {}):
                        continue
                return p
    return None


def load_options(path: Path | None, **overrides) -> Options:
    opts = Options()
    if path is not None and _toml is not None:
        data = _toml.loads(path.read_text(encoding="utf-8"))
        section = data.get("tool", {}).get("dagshift", {}) if path.name == "pyproject.toml" else data
        known = {f.name for f in fields(Options)}
        kwargs = {k.replace("-", "_"): v for k, v in section.items()}
        unknown = set(kwargs) - known
        if unknown:
            raise ValueError(f"unknown dagshift option(s) in {path}: {', '.join(sorted(unknown))}")
        opts = opts.merged(**{k: _coerce(k, v) for k, v in kwargs.items()})
    return opts.merged(**{k: _coerce(k, v) for k, v in overrides.items()})
