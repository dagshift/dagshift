"""Rewrite legacy Airflow template variables inside Jinja text.

Used for Python string literals, YAML/JSON configs and any other text that carries
Airflow templates. Works on raw text, so it also covers Jinja kept inside
``{% raw %}`` blocks by DAG factories (those blocks are Airflow templates at runtime).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .findings import Severity

_BLOCK = re.compile(r"\{\{(.*?)\}\}|\{%(.*?)%\}", re.DOTALL)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Same value under a new name (the new names exist since Airflow 2.2; the old ones are gone in 3.0).
RENAMES = {
    "execution_date": "logical_date",
    "next_execution_date": "data_interval_end",
    "prev_execution_date_success": "prev_data_interval_start_success",
    "triggering_dataset_events": "triggering_asset_events",
}
# Same value written as an expression. {q} is the quote character to use.
EXPRESSIONS = {
    "next_ds": "data_interval_end | ds",
    "next_ds_nodash": "data_interval_end | ds_nodash",
    "tomorrow_ds": "macros.ds_add(ds, 1)",
    "yesterday_ds": "macros.ds_add(ds, -1)",
    "tomorrow_ds_nodash": "macros.ds_format(macros.ds_add(ds, 1), {q}%Y-%m-%d{q}, {q}%Y%m%d{q})",
    "yesterday_ds_nodash": "macros.ds_format(macros.ds_add(ds, -1), {q}%Y-%m-%d{q}, {q}%Y%m%d{q})",
}
# Removed with no exact equivalent: reported with a suggestion.
MANUAL = {
    "prev_ds": (
        "`prev_ds` (logical date of the previous scheduled run) is removed. Closest: "
        "`{{ prev_data_interval_start_success | ds }}` (previous *successful* run), or for a fixed "
        "schedule `{{ macros.ds_add(ds, -1) }}` (daily). Pick the one that matches your intent."
    ),
    "prev_ds_nodash": (
        "`prev_ds_nodash` is removed. Closest: `{{ prev_data_interval_start_success | ds_nodash }}` "
        "(previous successful run) or a ds_add/ds_format expression for a fixed schedule."
    ),
    "prev_execution_date": (
        "`prev_execution_date` is removed. Closest: `{{ prev_data_interval_start_success }}` "
        "(previous successful run). There is no 'previous scheduled run' variable in Airflow 3."
    ),
    "conf": (
        "The `conf` (Airflow configuration) template variable is removed. Pass the value through "
        "`params` or a Variable, or read it in Python code instead."
    ),
}
_RUNTIME_OBJECTS = {"dag_run", "dagrun", "dr", "ti", "task_instance"}
_ATTR_RENAMES = {"execution_date": "logical_date"}
LEGACY_KEYS = frozenset(RENAMES) | frozenset(EXPRESSIONS) | frozenset(MANUAL)
DEPRECATED_SINCE = "2.2"  # Airflow deprecated these context keys in 2.2 and removed them in 3.0


@dataclass(frozen=True)
class ContextPolicy:
    """What to do with legacy context keys for one source -> target upgrade."""

    removed: frozenset  # legacy keys that no longer exist on the target
    available: frozenset  # context keys the target provides
    modernize: bool  # also rewrite keys that still work but are deprecated on the target
    target: str
    xcom_semantics: bool  # crossing 3.0: xcom_pull() without task_ids changed meaning
    attr_rename: str | None  # "required" | "modernize" | None for dag_run/ti .execution_date

    @classmethod
    def from_kb(cls, kb, options) -> "ContextPolicy":
        available = frozenset(kb.context_keys)
        removed = frozenset(k for k in LEGACY_KEYS if k not in available)
        modernize = bool(options.modernize) and kb.target_at_least(DEPRECATED_SINCE)
        if kb.target_at_least("3.0"):
            attr = "required"
        elif modernize:
            attr = "modernize"
        else:
            attr = None
        return cls(removed, available, modernize, kb.target, kb.crosses("3.0"), attr)

    def action(self, name: str) -> tuple[str, bool] | None:
        """(kind, required) for a legacy key: kind is rename | expr | manual."""
        if name not in LEGACY_KEYS:
            return None
        required = name in self.removed
        if not required and not self.modernize:
            return None
        if name in RENAMES:
            if RENAMES[name] in self.available:
                return "rename", required
            return ("manual", True) if required else None
        if name in EXPRESSIONS:
            base = "data_interval_end" if name.startswith("next") else "ds"
            if base in self.available:
                return "expr", required
            return ("manual", True) if required else None
        return ("manual", True) if required else None

    def why(self, name: str, required: bool) -> str:
        if required:
            return f"`{name}` does not exist in Airflow {self.target}"
        return f"`{name}` is deprecated since Airflow {DEPRECATED_SINCE}"


DEFAULT_POLICY = ContextPolicy(frozenset(LEGACY_KEYS), frozenset({"logical_date", "data_interval_end", "ds",
                               "triggering_asset_events", "prev_data_interval_start_success", "macros"}),
                               False, "3", True, "required")


@dataclass
class TextIssue:
    rule_id: str
    severity: Severity
    offset: int
    message: str
    recipe: str | None = None


def _strings_mask(expr: str) -> list[bool]:
    """True for characters inside quoted literals within a Jinja expression."""
    mask = [False] * len(expr)
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch in "'\"":
            q = ch
            j = i + 1
            while j < n and expr[j] != q:
                j += 2 if expr[j] == "\\" else 1
            for k in range(i, min(j + 1, n)):
                mask[k] = True
            i = j + 1
        else:
            i += 1
    return mask


def _prev_significant(expr: str, idx: int) -> str:
    j = idx - 1
    while j >= 0 and expr[j] in " \t\r\n":
        j -= 1
    return expr[j] if j >= 0 else ""


def _next_significant(expr: str, idx: int) -> str:
    j = idx
    while j < len(expr) and expr[j] in " \t\r\n":
        j += 1
    return expr[j] if j < len(expr) else ""


def _root_before_dot(expr: str, dot_idx: int) -> str:
    j = dot_idx - 1
    while j >= 0 and expr[j] in " \t":
        j -= 1
    end = j + 1
    while j >= 0 and (expr[j].isalnum() or expr[j] == "_"):
        j -= 1
    return expr[j + 1 : end]


def rewrite_jinja(
    text: str,
    quote_for: Callable[[int], str] | None = None,
    policy: ContextPolicy | None = None,
) -> tuple[str, list[TextIssue]]:
    """Return (new_text, issues). Offsets in issues refer to the *original* text."""
    if "{{" not in text and "{%" not in text:
        return text, []
    policy = policy or DEFAULT_POLICY
    issues: list[TextIssue] = []
    out: list[str] = []
    last = 0
    for m in _BLOCK.finditer(text):
        group = 1 if m.group(1) is not None else 2
        expr = m.group(group)
        expr_start = m.start(group)
        in_str = _strings_mask(expr)
        pieces: list[str] = []
        cursor = 0
        whole = expr.strip()
        for im in _IDENT.finditer(expr):
            name, s, e = im.group(0), im.start(), im.end()
            if in_str[s] or (s > 0 and (expr[s - 1].isalnum() or expr[s - 1] == "_")):
                continue
            prev = _prev_significant(expr, s)
            nxt = _next_significant(expr, e)
            abs_off = expr_start + s
            if prev == ".":
                # Attribute access: dag_run.execution_date / ti.execution_date
                dot = expr.rfind(".", 0, s)
                root = _root_before_dot(expr, dot)
                if root in _RUNTIME_OBJECTS and name in _ATTR_RENAMES and policy.attr_rename:
                    pieces.append(expr[cursor:s] + _ATTR_RENAMES[name])
                    cursor = e
                    why = ("is gone in Airflow " + policy.target if policy.attr_rename == "required"
                           else "is deprecated since Airflow " + DEPRECATED_SINCE)
                    issues.append(TextIssue(
                        "DS304", Severity.FIXED, abs_off,
                        f"`{root}.{name}` {why}; now `{root}.{_ATTR_RENAMES[name]}` (same value).",
                    ))
                continue
            if prev == "|":
                continue  # filter name
            if nxt == "=":
                j = e
                while j < len(expr) and expr[j] in " \t":
                    j += 1
                if expr[j : j + 2] != "==":
                    continue  # keyword argument or {% set x = ... %}
            act = policy.action(name)
            if act is None:
                continue
            kind, required = act
            if kind == "rename":
                pieces.append(expr[cursor:s] + RENAMES[name])
                cursor = e
                issues.append(TextIssue(
                    "DS301", Severity.FIXED, abs_off,
                    f"Template variable {policy.why(name, required)}; renamed to `{RENAMES[name]}` (same value).",
                ))
            elif kind == "expr":
                q = quote_for(abs_off) if quote_for is not None else "'"
                repl = EXPRESSIONS[name].format(q=q)
                if whole != name:
                    repl = f"({repl})"
                pieces.append(expr[cursor:s] + repl)
                cursor = e
                issues.append(TextIssue(
                    "DS301", Severity.FIXED, abs_off,
                    f"Template variable {policy.why(name, required)}; replaced with "
                    f"`{EXPRESSIONS[name].format(q=q)}` (same value).",
                ))
            else:
                issues.append(TextIssue(
                    "DS301", Severity.MANUAL, abs_off,
                    f"Template variable `{name}` does not exist in Airflow {policy.target}.", MANUAL.get(name),
                ))
        # xcom_pull without task_ids pulls only from the *current* task from Airflow 3.0 on.
        if policy.xcom_semantics:
            for xm in re.finditer(r"xcom_pull\s*\(", expr):
                if in_str[xm.start()]:
                    continue
                depth, j = 1, xm.end()
                while j < len(expr) and depth:
                    depth += {"(": 1, ")": -1}.get(expr[j], 0)
                    j += 1
                args = expr[xm.end() : j - 1].strip()
                first = args.split(",", 1)[0].strip() if args else ""
                has_task_ids = "task_ids" in args or (first and "=" not in first)
                if not has_task_ids:
                    issues.append(TextIssue(
                        "DS303", Severity.WARN, expr_start + xm.start(),
                        "xcom_pull() without task_ids reads only the current task's XComs from Airflow 3.0 on "
                        "(Airflow 2 searched every task in the run).",
                        "Pass task_ids explicitly, e.g. `ti.xcom_pull(task_ids='producer', key='...')`.",
                    ))
        if cursor:
            pieces.append(expr[cursor:])
            new_expr = "".join(pieces)
            out.append(text[last:expr_start])
            out.append(new_expr)
            last = expr_start + len(expr)
    out.append(text[last:])
    return "".join(out), issues


INTERVAL_SENSITIVE = {
    "ds", "ds_nodash", "ts", "ts_nodash", "ts_nodash_with_tz", "logical_date", "execution_date",
    "data_interval_start", "data_interval_end", "prev_ds", "next_ds", "yesterday_ds", "tomorrow_ds",
    "prev_ds_nodash", "next_ds_nodash", "yesterday_ds_nodash", "tomorrow_ds_nodash",
    "next_execution_date", "prev_execution_date",
}


def jinja_names(text: str) -> set[str]:
    """Top-level variable names referenced inside Jinja blocks (filters and attributes excluded)."""
    names: set[str] = set()
    for m in _BLOCK.finditer(text):
        expr = m.group(1) if m.group(1) is not None else m.group(2)
        in_str = _strings_mask(expr)
        for im in _IDENT.finditer(expr):
            s = im.start()
            if in_str[s] or (s > 0 and (expr[s - 1].isalnum() or expr[s - 1] == "_")):
                continue
            if _prev_significant(expr, s) in (".", "|"):
                continue
            names.add(im.group(0))
    return names


_DOTTED = re.compile(r"(?<![\w.])airflow(?:\.[A-Za-z_][A-Za-z0-9_]*)+")


def find_dotted_paths(text: str):
    """Yield (start, end, path) for every dotted airflow.* path in text."""
    for m in _DOTTED.finditer(text):
        yield m.start(), m.end(), m.group(0)
