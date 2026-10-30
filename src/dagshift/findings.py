"""Finding model shared by every rule."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class Severity(str, Enum):
    """How a finding was handled.

    FIXED   the codemod rewrote the code; semantics preserved
    MANUAL  breaks on Airflow 3 and has no safe automatic rewrite; a recipe is attached
    WARN    works on Airflow 3 but is likely to misbehave (silent semantic change)
    INFO    worth knowing before cutover; no code change needed
    """

    FIXED = "fixed"
    MANUAL = "manual"
    WARN = "warn"
    INFO = "info"


BLOCKING = {Severity.MANUAL}


@dataclass
class Finding:
    rule_id: str
    severity: Severity
    path: str
    line: int
    message: str
    recipe: str | None = None
    rule_name: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class FileResult:
    path: str
    original: str
    migrated: str
    findings: list[Finding] = field(default_factory=list)
    error: str | None = None
    templated: bool = False
    kind: str = ""
    providers: set[str] = field(default_factory=set)
    source_path: str | None = None  # absolute path on disk, when read from disk
    root: str | None = None  # the root (directory or file) this file was found under

    @property
    def changed(self) -> bool:
        return self.original != self.migrated

    def count(self, severity: Severity) -> int:
        return sum(1 for f in self.findings if f.severity == severity)
