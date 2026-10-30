"""Task context in Python code.

DS302  removed context keys: context["execution_date"], kwargs.get("next_ds"), def f(execution_date)
DS303  ti.xcom_pull() without task_ids
DS304  dag_run.execution_date / ti.execution_date attributes
"""
from __future__ import annotations

import libcst as cst
from libcst.metadata import (
    ExpressionContext,
    ExpressionContextProvider,
    PositionProvider,
    QualifiedNameProvider,
    ScopeProvider,
)

from ...findings import Severity
from ...jinja_text import INTERVAL_SENSITIVE, MANUAL, RENAMES
from ..base import Pass
from ..helpers import callee_name, is_task_decorator, kwarg

_DEFAULT_RECEIVERS = {"context", "ctx", "kwargs", "kw", "ti_context", "task_context"}
_RUNTIME_OBJECTS = {"dag_run", "dagrun", "dr", "ti", "task_instance"}
_RUNTIME_KEYS = {"dag_run", "ti", "task_instance"}

_PY_EXPRESSIONS = {
    "next_ds": ("data_interval_end", 'strftime("%Y-%m-%d")'),
    "next_ds_nodash": ("data_interval_end", 'strftime("%Y%m%d")'),
    "tomorrow_ds": ("logical_date", 'add(days=1).strftime("%Y-%m-%d")'),
    "yesterday_ds": ("logical_date", 'add(days=-1).strftime("%Y-%m-%d")'),
    "tomorrow_ds_nodash": ("logical_date", 'add(days=1).strftime("%Y%m%d")'),
    "yesterday_ds_nodash": ("logical_date", 'add(days=-1).strftime("%Y%m%d")'),
}


class _CallableCollector(cst.CSTVisitor):
    """Names of functions handed to operators as python_callable=..."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Call(self, node: cst.Call) -> None:
        for key in ("python_callable",):
            a = kwarg(node, key)
            if a is not None and isinstance(a.value, cst.Name):
                self.names.add(a.value.value)


class ContextPass(Pass):
    DESCRIPTION = "Task context keys and runtime attributes"
    METADATA_DEPENDENCIES = (PositionProvider, ScopeProvider, ExpressionContextProvider, QualifiedNameProvider)

    def __init__(self, context, state) -> None:
        super().__init__(context, state)
        self.policy = state.policy
        self._receivers: list[set[str]] = [set(_DEFAULT_RECEIVERS)]
        self._callables: set[str] = set()
        self._renames: dict[int, str] = {}

    def visit_Module(self, node: cst.Module) -> None:
        c = _CallableCollector()
        node.visit(c)
        self._callables = c.names

    # -- receivers in scope ------------------------------------------------------------
    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        names = set(self._receivers[-1])
        star = node.params.star_kwarg
        if star is not None:
            names.add(star.name.value)
        self._receivers.append(names)
        is_task_fn = node.name.value in self._callables or any(
            is_task_decorator(self, d.decorator) for d in node.decorators
        )
        if is_task_fn:
            self._check_params(node)

    def leave_FunctionDef(self, original_node, updated_node):
        self._receivers.pop()
        return updated_node

    def visit_Assign(self, node: cst.Assign) -> None:
        if isinstance(node.value, cst.Call) and callee_name(node.value) == "get_current_context":
            for t in node.targets:
                if isinstance(t.target, cst.Name):
                    self._receivers[-1].add(t.target.value)

    def _is_receiver(self, node: cst.BaseExpression) -> bool:
        return isinstance(node, cst.Name) and node.value in self._receivers[-1]

    # -- def f(execution_date, **kwargs) -----------------------------------------------
    def _check_params(self, fn: cst.FunctionDef) -> None:
        params = [*fn.params.params, *fn.params.kwonly_params, *fn.params.posonly_params]
        existing = {p.name.value for p in params}
        for p in params:
            name = p.name.value
            if name in INTERVAL_SENSITIVE:
                self.state.data_interval_sensitive = True
            act = self.policy.action(name)
            if act is None or not self.enabled("DS302"):
                continue
            kind, required = act
            why = self.policy.why(name, required)
            if kind == "rename" and RENAMES[name] not in existing:
                new = RENAMES[name]
                try:
                    scope = self.get_metadata(ScopeProvider, fn.body.body[0] if isinstance(fn.body, cst.IndentedBlock) else fn.body)
                except Exception:
                    scope = None
                if scope is None or new in scope.assignments:
                    if required:
                        self.report("DS302", Severity.MANUAL, p,
                                    f"Parameter `{name}` of task callable `{fn.name.value}`: {why}; rename it to "
                                    f"`{new}` (a local `{new}` already exists).")
                    continue
                self._renames[id(p.name)] = new
                for assignment in scope.assignments[name]:
                    for ref in assignment.references:
                        if isinstance(ref.node, cst.Name):
                            self._renames[id(ref.node)] = new
                self.report("DS302", Severity.FIXED, p,
                            f"Context parameter `{name}` of `{fn.name.value}`: {why}; renamed to `{new}` (same value).")
            elif required:
                hint = MANUAL.get(name) or f"Read the value from `{_PY_EXPRESSIONS.get(name, ('logical_date',))[0]}` instead."
                self.report("DS302", Severity.MANUAL, p,
                            f"Parameter `{name}` of task callable `{fn.name.value}` is not injected in "
                            f"Airflow {self.kb.target}.", hint)

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name):
        new = self._renames.get(id(original_node))
        return updated_node.with_changes(value=new) if new else updated_node

    # -- context["key"] ----------------------------------------------------------------
    def leave_Subscript(self, original_node: cst.Subscript, updated_node: cst.Subscript):
        if not self._is_receiver(original_node.value) or len(original_node.slice) != 1:
            return updated_node
        el = original_node.slice[0].slice
        if not (isinstance(el, cst.Index) and isinstance(el.value, cst.SimpleString)):
            return updated_node
        key = el.value.evaluated_value
        if key in INTERVAL_SENSITIVE:
            self.state.data_interval_sensitive = True
        act = self.policy.action(key)
        if act is None or not self.enabled("DS302"):
            return updated_node
        kind, required = act
        why = self.policy.why(key, required)
        recv = original_node.value.value  # type: ignore[union-attr]
        try:
            ctx = self.get_metadata(ExpressionContextProvider, original_node)
        except Exception:
            ctx = ExpressionContext.LOAD
        if kind == "rename":
            q = el.value.value[0] if el.value.value[0] in "'\"" else '"'
            new_slice = [original_node.slice[0].with_changes(
                slice=el.with_changes(value=cst.SimpleString(f"{q}{RENAMES[key]}{q}")))]
            self.report("DS302", Severity.FIXED, original_node,
                        f'`{recv}["{key}"]`: {why}; now `{recv}["{RENAMES[key]}"]` (same value).')
            return updated_node.with_changes(slice=new_slice)
        if kind == "expr" and key in _PY_EXPRESSIONS and ctx == ExpressionContext.LOAD:
            base, tail = _PY_EXPRESSIONS[key]
            expr = cst.parse_expression(f'{recv}["{base}"].{tail}')
            self.report("DS302", Severity.FIXED, original_node,
                        f'`{recv}["{key}"]`: {why}; replaced with `{recv}["{base}"].{tail}`.')
            return expr
        if required:
            self.report("DS302", Severity.MANUAL, original_node,
                        f'`{recv}["{key}"]` does not exist in the Airflow {self.kb.target} task context.', MANUAL.get(key))
        return updated_node

    # -- context.get("key") / ti.xcom_pull() --------------------------------------------
    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call):
        f = original_node.func
        if (isinstance(f, cst.Attribute) and f.attr.value == "xcom_pull" and self.enabled("DS303")
                and self.policy.xcom_semantics):
            pos = [a for a in original_node.args if a.keyword is None and not a.star]
            stars = [a for a in original_node.args if a.star]
            if not pos and not stars and kwarg(original_node, "task_ids") is None:
                self.report("DS303", Severity.WARN, original_node,
                            "xcom_pull() without task_ids reads only the current task's XComs from Airflow 3.0 on "
                            "(Airflow 2 searched every task in the run).",
                            "Pass task_ids explicitly: ti.xcom_pull(task_ids='producer', key='...').")
        if (isinstance(f, cst.Attribute) and f.attr.value == "get" and self._is_receiver(f.value)
                and original_node.args and isinstance(original_node.args[0].value, cst.SimpleString)):
            key = original_node.args[0].value.evaluated_value
            if key in INTERVAL_SENSITIVE:
                self.state.data_interval_sensitive = True
            act = self.policy.action(key)
            if act is not None and self.enabled("DS302"):
                kind, required = act
                recv = f.value.value  # type: ignore[union-attr]
                if kind == "rename":
                    q = original_node.args[0].value.value[0]
                    args = list(updated_node.args)
                    args[0] = args[0].with_changes(value=cst.SimpleString(f"{q}{RENAMES[key]}{q}"))
                    self.report("DS302", Severity.FIXED, original_node,
                                f'`{recv}.get("{key}")`: {self.policy.why(key, required)}; now '
                                f'`{recv}.get("{RENAMES[key]}")` (same value).')
                    return updated_node.with_changes(args=args)
                if required:
                    self.report("DS302", Severity.MANUAL, original_node,
                                f'`{recv}.get("{key}")` returns nothing in Airflow {self.kb.target}: the key was removed.',
                                MANUAL.get(key) or (
                                    f'Use `{recv}["{_PY_EXPRESSIONS[key][0]}"].{_PY_EXPRESSIONS[key][1]}`.'
                                    if key in _PY_EXPRESSIONS else None))
        return updated_node

    # -- dag_run.execution_date --------------------------------------------------------
    def leave_Attribute(self, original_node: cst.Attribute, updated_node: cst.Attribute):
        if original_node.attr.value != "execution_date" or not self.enabled("DS304") or not self.policy.attr_rename:
            return updated_node
        v = original_node.value
        root_ok = isinstance(v, cst.Name) and v.value in _RUNTIME_OBJECTS
        if isinstance(v, cst.Subscript) and self._is_receiver(v.value) and len(v.slice) == 1:
            el = v.slice[0].slice
            root_ok = isinstance(el, cst.Index) and isinstance(el.value, cst.SimpleString) and \
                el.value.evaluated_value in _RUNTIME_KEYS
        if not root_ok:
            return updated_node
        self.state.data_interval_sensitive = True
        why = (f"is gone in Airflow {self.kb.target}" if self.policy.attr_rename == "required"
               else "is deprecated since Airflow 2.2")
        self.report("DS304", Severity.FIXED, original_node,
                    f"`.execution_date` on the DAG run / task instance {why}; now `.logical_date` (same value).")
        return updated_node.with_changes(attr=cst.Name("logical_date"))
