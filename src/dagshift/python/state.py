"""Shared state for the Python codemod passes of one file."""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from ..findings import Finding, Severity
from ..jinja_mask import MaskedSource
from ..jinja_text import ContextPolicy
from ..kb import KnowledgeBase
from ..options import Options


class LineMap:
    """Maps line numbers of an intermediate version of a file back to the original."""

    def __init__(self, original: str, current: str) -> None:
        self._map: dict[int, int] = {}
        a = original.splitlines()
        b = current.splitlines()
        sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            for k in range(j2 - j1):
                if tag == "equal":
                    self._map[j1 + k + 1] = i1 + k + 1
                else:
                    self._map[j1 + k + 1] = min(i1 + k, max(i2 - 1, i1)) + 1
        self._last = len(a)

    def __call__(self, line: int) -> int:
        return self._map.get(line, min(line, max(self._last, 1)))


@dataclass
class FileState:
    path: str
    kb: KnowledgeBase
    options: Options
    masked: MaskedSource
    original: str
    findings: list[Finding] = field(default_factory=list)
    line_map: LineMap | None = None
    # facts collected across passes
    provider_modules: set[str] = field(default_factory=set)
    data_interval_sensitive: bool = False
    imports_airflow: bool = False
    policy: ContextPolicy | None = None

    def add(self, rule_id: str, severity: Severity, line: int, message: str,
            recipe: str | None = None, rule_name: str = "") -> None:
        if not self.options.rule_enabled(rule_id):
            return
        orig_line = self.line_map(line) if self.line_map else line
        key = (rule_id, orig_line, message)
        if any((f.rule_id, f.line, f.message) == key for f in self.findings):
            return
        self.findings.append(Finding(rule_id, severity, self.path, orig_line, message, recipe, rule_name))

    def enabled(self, rule_id: str) -> bool:
        return self.options.rule_enabled(rule_id)
