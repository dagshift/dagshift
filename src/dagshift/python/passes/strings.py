"""String literals: Airflow templates inside them, and Airflow code emitted by generators.

DS301  removed template variables in Jinja strings (fixed where an exact equivalent exists)
DS303  xcom_pull() without task_ids inside templates
DS304  dag_run.execution_date / ti.execution_date inside templates
DS601  Airflow code embedded in a string literal (code generators) migrated recursively
DS602  legacy Airflow names inside string literals (dotted paths, Dataset(...))
"""
from __future__ import annotations

import re

import libcst as cst

from ...findings import Severity
from ...jinja_text import INTERVAL_SENSITIVE, find_dotted_paths, jinja_names, rewrite_jinja
from ..base import Pass

_CODEGEN_MARKER = re.compile(r"^\s*(from\s+airflow|import\s+airflow)|\bDAG\s*\(|@dag\b|@task\b", re.M)
# (pattern, replacement, message, minimum target, rewrite only when crossing this version)
_LEGACY_CALLS = [
    (re.compile(r"\bDataset\((?=[\"'f{a-zA-Z_])"), "Asset(", "Dataset(...) is Asset(...) from Airflow 3.0.", "3.0"),
    (re.compile(r"\bDatasetAlias\("), "AssetAlias(", "DatasetAlias(...) is AssetAlias(...) from Airflow 3.0.", "3.0"),
    (re.compile(r"\bschedule_interval\s*="), "schedule=", "DAG(schedule_interval=...) is DAG(schedule=...).", "2.4"),
]
_MAX_DEPTH = 2


def _split_prefix(raw: str) -> tuple[str, str, str]:
    """('rb', '\"\"\"', body) for a SimpleString's raw code."""
    i = 0
    while i < len(raw) and raw[i] not in "'\"":
        i += 1
    prefix, rest = raw[:i], raw[i:]
    q = rest[:3] if rest[:3] in ('"""', "'''") else rest[:1]
    return prefix, q, rest[len(q) : len(rest) - len(q)]


class StringsPass(Pass):
    DESCRIPTION = "Templates and generated code inside string literals"

    def __init__(self, context, state, depth: int = 0) -> None:
        super().__init__(context, state)
        self.depth = depth
        self._docstrings: set[int] = set()

    # -- docstrings are documentation, not code ---------------------------------------
    def _mark_docstring(self, body) -> None:
        if body and isinstance(body[0], cst.SimpleStatementLine) and body[0].body:
            expr = body[0].body[0]
            if isinstance(expr, cst.Expr) and isinstance(expr.value, (cst.SimpleString, cst.ConcatenatedString)):
                self._docstrings.add(id(expr.value))

    def visit_Module(self, node: cst.Module) -> None:
        self._mark_docstring(node.body)

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        if isinstance(node.body, cst.IndentedBlock):
            self._mark_docstring(node.body.body)

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        if isinstance(node.body, cst.IndentedBlock):
            self._mark_docstring(node.body.body)

    # -- helpers ---------------------------------------------------------------------
    def _issues_to_findings(self, node: cst.CSTNode, text: str, issues) -> None:
        base = self.line_of(node)
        for iss in issues:
            line = base + text.count("\n", 0, iss.offset)
            self.report(iss.rule_id, iss.severity, line, iss.message, iss.recipe)

    def _note_interval_usage(self, text: str) -> None:
        if "{{" in text or "{%" in text:
            if jinja_names(text) & INTERVAL_SENSITIVE:
                self.state.data_interval_sensitive = True

    def _rewrite_text(self, node: cst.CSTNode, body: str, quote: str, codeish: bool) -> str:
        """Jinja + legacy-name rewrites on the raw body of a string literal."""
        other = "'" if quote.startswith('"') else '"'
        new = body
        if self.enabled("DS301") or self.enabled("DS303") or self.enabled("DS304"):
            self._note_interval_usage(body)
            new, issues = rewrite_jinja(body, quote_for=lambda _off: other, policy=self.state.policy)
            self._issues_to_findings(node, body, issues)
            if not self.enabled("DS301") and not self.enabled("DS304"):
                new = body
        if self.options.migrate_strings and self.enabled("DS602"):
            new = self._rewrite_legacy_names(node, new, codeish)
        return new

    def _rewrite_legacy_names(self, node: cst.CSTNode, text: str, codeish: bool) -> str:
        out, last, changed = [], 0, []
        for s, e, path in find_dotted_paths(text):
            mv = self.kb.lookup(path)
            if mv and mv.new and (mv.required or self.options.modernize) and mv.new != path:
                out.append(text[last:s] + mv.new)
                last = e
                changed.append(f"{path} -> {mv.new}")
            elif mv and not mv.new and mv.status == "removed":
                self.report("DS602", Severity.MANUAL, node,
                            f"String references `{path}`, which does not exist in Airflow {self.kb.target}.",
                            mv.note)
        out.append(text[last:])
        text = "".join(out)
        if codeish and self.state.imports_airflow:
            for rx, repl, msg, since in _LEGACY_CALLS:
                if not self.kb.target_at_least(since):
                    continue
                if since == "2.4" and not (self.options.modernize or self.kb.target_at_least("3.0")):
                    continue
                if rx.search(text):
                    text = rx.sub(repl, text)
                    changed.append(msg)
        for c in changed:
            self.report("DS602", Severity.FIXED, node, f"Updated Airflow name inside a string literal: {c}")
        return text

    def _try_nested(self, node: cst.SimpleString, value: str) -> str | None:
        if self.depth >= _MAX_DEPTH or not self.options.migrate_strings or not self.enabled("DS601"):
            return None
        if "airflow" not in value or "\n" not in value or not _CODEGEN_MARKER.search(value):
            return None
        from ..migrator import migrate_python_source  # local import: recursion

        res = migrate_python_source(value, self.state.path, self.kb, self.options, depth=self.depth + 1)
        if res.error or res.migrated == value:
            return None
        base = self.line_of(node)
        fixed = sorted({f.rule_id for f in res.findings if f.severity == Severity.FIXED})
        for f in res.findings:
            if f.severity != Severity.FIXED:
                self.report(f.rule_id, f.severity, base + f.line - 1, f"(in generated code) {f.message}", f.recipe)
        self.report("DS601", Severity.FIXED, node,
                    f"Migrated Airflow code embedded in a string literal ({', '.join(fixed) or 'changes'}).")
        return res.migrated

    # -- leave handlers --------------------------------------------------------------
    def leave_SimpleString(self, original_node: cst.SimpleString, updated_node: cst.SimpleString):
        if id(original_node) in self._docstrings:
            return updated_node
        prefix, quote, body = _split_prefix(updated_node.value)
        body_unmasked = self.state.masked.unmask(body)
        if "b" in prefix.lower():
            return updated_node
        nested = None
        if len(quote) == 3 and "\\" not in body_unmasked:
            nested = self._try_nested(original_node, body_unmasked)
            if nested is not None and (quote not in nested) and "\\" not in nested:
                return updated_node.with_changes(value=f"{prefix}{quote}{nested}{quote}")
        new_body = self._rewrite_text(original_node, body_unmasked, quote, codeish=True)
        if new_body != body_unmasked:
            if len(quote) == 1 and new_body.count(quote) > body_unmasked.count(quote):
                return updated_node  # cannot safely re-quote; findings already reported
            return updated_node.with_changes(value=f"{prefix}{quote}{new_body}{quote}")
        if body_unmasked != body:
            return updated_node.with_changes(value=f"{prefix}{quote}{body_unmasked}{quote}")
        return updated_node

    def leave_FormattedString(self, original_node: cst.FormattedString, updated_node: cst.FormattedString):
        # Flatten to text with sentinels for {expressions}; braces are doubled in f-string source.
        parts = updated_node.parts
        sentinels, chunks = [], []
        for i, p in enumerate(parts):
            if isinstance(p, cst.FormattedStringText):
                chunks.append(self.state.masked.unmask(p.value).replace("{{", "{").replace("}}", "}"))
            else:
                tok = f"__DSFX{i}__"
                sentinels.append((tok, p))
                chunks.append(tok)
        text = "".join(chunks)
        quote = updated_node.start.lstrip("fFrRbBuU") or '"'
        new = self._rewrite_text(original_node, text, quote, codeish=True)
        if new == text:
            return updated_node
        if any(new.count(tok) != 1 for tok, _ in sentinels):
            return updated_node
        new_parts: list[cst.BaseFormattedStringContent] = []
        rest = new
        for tok, expr_part in sentinels:
            before, rest = rest.split(tok, 1)
            if before:
                new_parts.append(cst.FormattedStringText(before.replace("{", "{{").replace("}", "}}")))
            new_parts.append(expr_part)
        if rest:
            new_parts.append(cst.FormattedStringText(rest.replace("{", "{{").replace("}", "}}")))
        if len(quote) == 1 and new.count(quote) > text.count(quote):
            return updated_node
        return updated_node.with_changes(parts=new_parts)
