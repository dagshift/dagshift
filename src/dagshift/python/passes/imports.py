"""Import paths.

DS101  import moved: the old path is removed or deprecated on the target -> rewritten
DS102  import modernized: the old path still works but has an airflow.sdk home -> rewritten
       (controlled by Options.modernize)
DS103  import removed with no drop-in replacement -> manual
DS104  module family removed in Airflow 3 (airflow.contrib, airflow.www, airflow.api.client, ...)
"""
from __future__ import annotations

import libcst as cst
import libcst.matchers as m
from libcst.metadata import PositionProvider, QualifiedNameProvider, QualifiedNameSource, ScopeProvider
from libcst.metadata.scope_provider import ImportAssignment

from ...findings import Severity
from ...kb import Move
from ..base import Pass
from ..helpers import dotted

_REMOVED_FAMILIES = {
    "airflow.contrib": "airflow.contrib was removed (Airflow 2.0). Import from the matching provider package.",
    "airflow.www": "The Flask webserver (airflow.www) is gone in Airflow 3; the UI is a React app served by "
                   "the API server. Port views to FastAPI plugins (fastapi_apps / external_views / react_apps).",
    "airflow.api.client": "The Python API client in airflow.api.client is removed. Use apache-airflow-client "
                          "against the REST API v2.",
    "airflow.api.common.experimental": "airflow.api.common.experimental is removed. Use the REST API v2.",
    "airflow.api_connexion": "The connexion-based REST API v1 is removed; Airflow 3 serves /api/v2 via FastAPI.",
    "airflow.operators.subdag": "SubDAGs are removed in Airflow 3. Rebuild as a TaskGroup or a separate DAG.",
    "airflow.operators.subdag_operator": "SubDAGs are removed in Airflow 3. Rebuild as a TaskGroup.",
}
_HANDLED_ELSEWHERE = {"airflow.utils.dates.days_ago", "airflow.utils.decorators.apply_defaults"}
_MERGEABLE_PREFIXES = ("airflow.sdk", "airflow.providers.")


_FAMILY_REMOVED_IN = {
    "airflow.contrib": "2.0", "airflow.www": "3.0", "airflow.api.client": "3.0",
    "airflow.api.common.experimental": "3.0", "airflow.api_connexion": "3.0",
    "airflow.operators.subdag": "3.0", "airflow.operators.subdag_operator": "3.0",
}


def _family(module: str, kb=None) -> str | None:
    for fam in _REMOVED_FAMILIES:
        if module == fam or module.startswith(fam + "."):
            if kb is not None and not kb.target_at_least(_FAMILY_REMOVED_IN[fam]):
                return None
            return fam
    return None


def _module_expr(path: str) -> cst.BaseExpression:
    parts = path.split(".")
    node: cst.BaseExpression = cst.Name(parts[0])
    for p in parts[1:]:
        node = cst.Attribute(value=node, attr=cst.Name(p))
    return node


class ImportsPass(Pass):
    DESCRIPTION = "Moved, renamed and removed import paths"
    METADATA_DEPENDENCIES = (PositionProvider, ScopeProvider, QualifiedNameProvider)

    def __init__(self, context, state) -> None:
        super().__init__(context, state)
        self.plans: dict[int, list[tuple[cst.ImportAlias, Move | None, str | None]]] = {}
        self.renames: dict[int, str] = {}
        self.touched: set[str] = set()
        # airflow.* plus the top-level packages of any custom moves (internal operator libraries)
        self._roots = {"airflow"} | {k.split(".")[0] for k in self.kb.extra_moves}

    # -- decide ----------------------------------------------------------------------------
    def _decide(self, full: str) -> tuple[Move | None, str | None]:
        mv = self.kb.lookup(full)
        if mv is None:
            return None, None
        if mv.new and mv.new != full:
            if mv.required:
                return mv, "DS101"
            if self.options.modernize and not mv.internal:
                return mv, "DS102"
            return None, None
        if not mv.new and mv.status == "removed" and full not in _HANDLED_ELSEWHERE:
            return mv, "DS103"
        return None, None

    def visit_Module(self, node: cst.Module) -> None:
        bound_names = self._module_bindings(node)
        for imp in m.findall(node, m.ImportFrom()):
            if imp.relative or imp.module is None:
                continue
            module = dotted(imp.module) or ""
            if module.split(".")[0] not in self._roots:
                continue
            fam = _family(module, self.kb)
            if isinstance(imp.names, cst.ImportStar):
                self.report("DS104", Severity.WARN, imp,
                            f"`from {module} import *` cannot be migrated automatically; list the names you use.")
                continue
            plan = []
            for alias in imp.names:
                name = alias.name.value if isinstance(alias.name, cst.Name) else dotted(alias.name)
                asname = alias.asname.name.value if alias.asname and isinstance(alias.asname.name, cst.Name) else None
                mv, rule = self._decide(f"{module}.{name}")
                if rule in ("DS101", "DS102") and not self.enabled(rule):
                    mv, rule = None, None
                if rule == "DS103":
                    if self.enabled("DS103"):
                        self.report("DS103", Severity.MANUAL, imp,
                                    f"`{module}.{name}` does not exist in Airflow {self.kb.target}.",
                                    mv.note if mv else None)
                    plan.append((alias, None, None))
                    continue
                use_alias = None
                if mv and mv.new_name != name and asname is None:
                    if mv.new_name in bound_names - {name}:
                        use_alias = name  # keep the old local name to avoid a clash
                    else:
                        self._plan_rename(imp, name, mv.new_name)
                plan.append((alias, mv, use_alias))
            if any(mv for _, mv, _ in plan):
                self.plans[id(imp)] = plan
            unresolved = [a for a, mv, _ in plan if mv is None]
            if fam and unresolved and self.enabled("DS104"):
                names = ", ".join(a.name.value for a in unresolved if isinstance(a.name, cst.Name))
                self.report("DS104", Severity.MANUAL, imp,
                            f"`from {module} import {names}`: {_REMOVED_FAMILIES[fam]}")

    def _module_bindings(self, node: cst.Module) -> set[str]:
        try:
            scope = self.get_metadata(ScopeProvider, node.body[0]) if node.body else None
        except Exception:
            scope = None
        if scope is None:
            return set()
        return {a.name for a in scope.assignments}

    def _plan_rename(self, imp: cst.ImportFrom, old: str, new: str) -> None:
        try:
            scope = self.get_metadata(ScopeProvider, imp)
        except Exception:
            return
        if scope is None:
            return
        for assignment in scope.assignments[old]:
            if isinstance(assignment, ImportAssignment) and assignment.node is imp:
                for ref in assignment.references:
                    if isinstance(ref.node, cst.Name):
                        self.renames[id(ref.node)] = new

    # -- apply -----------------------------------------------------------------------------
    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name):
        new = self.renames.get(id(original_node))
        return updated_node.with_changes(value=new) if new else updated_node

    def leave_SimpleStatementLine(self, original_node: cst.SimpleStatementLine,
                                  updated_node: cst.SimpleStatementLine):
        if not any(isinstance(s, cst.ImportFrom) and id(s) in self.plans for s in original_node.body):
            return updated_node
        new_lines: list[cst.SimpleStatementLine] = []
        others: list[cst.BaseSmallStatement] = []
        for orig, upd in zip(original_node.body, updated_node.body):
            if not (isinstance(orig, cst.ImportFrom) and id(orig) in self.plans):
                others.append(upd)
                continue
            for stmt in self._rewrite(orig):
                new_lines.append(cst.SimpleStatementLine(body=[stmt]))
        if others:
            new_lines.insert(0, updated_node.with_changes(body=others))
        if not new_lines:
            return cst.RemovalSentinel.REMOVE
        first = new_lines[0].with_changes(leading_lines=updated_node.leading_lines)
        last = new_lines[-1].with_changes(trailing_whitespace=updated_node.trailing_whitespace)
        if len(new_lines) == 1:
            return first.with_changes(trailing_whitespace=updated_node.trailing_whitespace)
        return cst.FlattenSentinel([first, *new_lines[1:-1], last])

    def _rewrite(self, imp: cst.ImportFrom) -> list[cst.ImportFrom]:
        module = dotted(imp.module) or ""
        keep: list[cst.ImportAlias] = []
        moved: dict[str, list[cst.ImportAlias]] = {}
        for alias, mv, use_alias in self.plans[id(imp)]:
            if mv is None or mv.new is None:
                keep.append(alias)
                continue
            asname = alias.asname
            if use_alias:
                asname = cst.AsName(name=cst.Name(use_alias))
            new_alias = cst.ImportAlias(name=cst.Name(mv.new_name), asname=asname)
            moved.setdefault(mv.new_module, []).append(new_alias)
            self.touched.add(mv.new_module)
            if mv.new_module.startswith("airflow.providers."):
                pkg = mv.new_module.split(".")[2]
                self.state.provider_modules.add(pkg)
            rule = "DS101" if mv.required else "DS102"
            why = {"removed": "removed", "deprecated": "deprecated", "custom": "custom move"}.get(mv.status, "")
            msg = f"`{module}.{alias.name.value}` -> `{mv.new}`"
            if rule == "DS101":
                msg += f" ({why} on Airflow {self.kb.target})"
            else:
                msg += " (still works, but airflow.sdk is the Airflow 3 authoring interface)"
            if use_alias:
                msg += f"; imported as `{use_alias}` because `{mv.new_name}` is already used in this module"
            self.report(rule, Severity.FIXED, imp, msg)
        out: list[cst.ImportFrom] = []
        if keep:
            out.append(_import_from(module, keep, imp))
        for new_module, aliases in moved.items():
            out.append(_import_from(new_module, aliases, imp))
        return out

    # -- airflow.operators.python.PythonOperator(...) style references -------------------
    def leave_Attribute(self, original_node: cst.Attribute, updated_node: cst.Attribute):
        try:
            names = self.get_metadata(QualifiedNameProvider, original_node, set())
        except Exception:
            return updated_node
        for q in names:
            if q.source is not QualifiedNameSource.IMPORT:
                continue
            mv, rule = self._decide(q.name)
            if rule not in ("DS101", "DS102") or mv is None or not self.enabled(rule):
                continue
            self.add_import(mv.new_module, mv.new_name)
            self.touched.add(mv.new_module)
            if mv.new_module.startswith("airflow.providers."):
                self.state.provider_modules.add(mv.new_module.split(".")[2])
            root = dotted(original_node) or ""
            self._drop_module_import(original_node, q.name.rpartition(".")[0])
            self.report(rule, Severity.FIXED, original_node,
                        f"`{root}` -> `{mv.new_name}` imported from `{mv.new_module}`")
            return cst.Name(mv.new_name)
        return updated_node

    def _drop_module_import(self, node: cst.Attribute, old_module: str) -> None:
        """Schedule removal of the import that bound the chain's root, once nothing uses it."""
        base = node
        while isinstance(base, cst.Attribute):
            base = base.value
        if not isinstance(base, cst.Name):
            return
        try:
            root_q = {q.name for q in self.get_metadata(QualifiedNameProvider, base, set())
                      if q.source is QualifiedNameSource.IMPORT}
        except Exception:
            root_q = set()
        for qn in root_q:
            if qn == base.value:  # import airflow.operators.bash
                self.remove_import_if_unused(old_module)
            else:  # from airflow import models  /  import airflow.models as m
                mod, _, obj = qn.rpartition(".")
                asname = base.value if base.value != obj else None
                self.remove_import_if_unused(mod, obj, asname)
                self.remove_import_if_unused(qn, None, base.value if asname else None)

    # -- merge duplicate "from airflow.sdk import ..." lines --------------------------------
    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module):
        body = list(updated_node.body)
        first_idx: dict[str, int] = {}
        drop: set[int] = set()
        for i, stmt in enumerate(body):
            if not (isinstance(stmt, cst.SimpleStatementLine) and len(stmt.body) == 1
                    and isinstance(stmt.body[0], cst.ImportFrom)):
                continue
            imp = stmt.body[0]
            if imp.relative or isinstance(imp.names, cst.ImportStar) or imp.module is None:
                continue
            mod = dotted(imp.module) or ""
            if mod not in self.touched or not mod.startswith(_MERGEABLE_PREFIXES):
                continue
            if mod not in first_idx:
                first_idx[mod] = i
                continue
            j = first_idx[mod]
            base = body[j].body[0]
            seen = {(a.name.value, a.asname.name.value if a.asname else None) for a in base.names}
            extra = [a for a in imp.names
                     if (a.name.value, a.asname.name.value if a.asname else None) not in seen]
            body[j] = body[j].with_changes(body=[_import_from(mod, list(base.names) + extra, base)])
            drop.add(i)
        if not drop:
            return updated_node
        return updated_node.with_changes(body=[s for i, s in enumerate(body) if i not in drop])


def _import_from(module: str, aliases: list[cst.ImportAlias], template: cst.ImportFrom) -> cst.ImportFrom:
    aliases = [a.with_changes(comma=cst.MaybeSentinel.DEFAULT) for a in aliases]
    names_code = ", ".join(cst.Module([]).code_for_node(a) for a in aliases)
    if len(f"from {module} import {names_code}") <= 99:
        fixed = []
        for i, a in enumerate(aliases):
            comma = cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")) if i < len(aliases) - 1 else cst.MaybeSentinel.DEFAULT
            fixed.append(a.with_changes(comma=comma))
        return cst.ImportFrom(module=_module_expr(module), names=fixed)
    # long: parenthesized, one name per line
    indent = cst.ParenthesizedWhitespace(
        first_line=cst.TrailingWhitespace(), empty_lines=[], indent=True,
        last_line=cst.SimpleWhitespace("    "),
    )
    closing = cst.ParenthesizedWhitespace(first_line=cst.TrailingWhitespace(), empty_lines=[], indent=True,
                                          last_line=cst.SimpleWhitespace(""))
    fixed = []
    for i, a in enumerate(aliases):
        fixed.append(a.with_changes(comma=cst.Comma(whitespace_after=indent if i < len(aliases) - 1 else closing)))
    return cst.ImportFrom(
        module=_module_expr(module), names=fixed,
        lpar=cst.LeftParen(whitespace_after=indent), rpar=cst.RightParen(),
    )
