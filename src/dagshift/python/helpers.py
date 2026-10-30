"""libcst helpers shared by the passes."""
from __future__ import annotations

import libcst as cst
import libcst.matchers as m
from libcst.metadata import QualifiedNameProvider

DAG_CLASSES = {
    "airflow.DAG", "airflow.models.DAG", "airflow.models.dag.DAG", "airflow.sdk.DAG",
    "airflow.sdk.definitions.dag.DAG",
}
DAG_DECORATORS = {
    "airflow.decorators.dag", "airflow.sdk.dag", "airflow.sdk.definitions.dag.dag",
    "airflow.models.dag.dag",
}
TASK_DECORATOR_ROOTS = {"airflow.decorators.task", "airflow.sdk.task"}
VARIABLE_GET = {
    "airflow.models.Variable.get", "airflow.models.variable.Variable.get", "airflow.sdk.Variable.get",
}
TRIGGER_DAGRUN = {
    "airflow.operators.trigger_dagrun.TriggerDagRunOperator",
    "airflow.operators.dagrun_operator.TriggerDagRunOperator",
    "airflow.providers.standard.operators.trigger_dagrun.TriggerDagRunOperator",
}


def qnames(visitor, node: cst.CSTNode) -> set[str]:
    try:
        return {q.name for q in visitor.get_metadata(QualifiedNameProvider, node, set())}
    except Exception:
        return set()


def dotted(node: cst.BaseExpression) -> str | None:
    """'a.b.c' for Name/Attribute chains, else None."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        left = dotted(node.value)
        return f"{left}.{node.attr.value}" if left else None
    return None


def callee_name(call: cst.Call) -> str | None:
    """Last name of the callee: PythonOperator for airflow.x.PythonOperator(...)."""
    f = call.func
    if isinstance(f, cst.Name):
        return f.value
    if isinstance(f, cst.Attribute):
        return f.attr.value
    return None


def kwarg(call: cst.Call, name: str) -> cst.Arg | None:
    for a in call.args:
        if a.keyword is not None and a.keyword.value == name:
            return a
    return None


def has_star_kwargs(call: cst.Call) -> bool:
    return any(a.star == "**" for a in call.args)


def positional(call: cst.Call) -> list[cst.Arg]:
    return [a for a in call.args if a.keyword is None and a.star == ""]


def is_dag_call(visitor, call: cst.Call) -> bool:
    names = qnames(visitor, call.func)
    if names & DAG_CLASSES or names & DAG_DECORATORS:
        return True
    # Unresolvable (templated/star import): fall back on the callee name.
    if not names and callee_name(call) == "DAG":
        return True
    return False


def is_task_decorator(visitor, node: cst.BaseExpression) -> bool:
    """@task, @task(), @task.python(...), @task.virtualenv, @task.bash, ..."""
    target = node.func if isinstance(node, cst.Call) else node
    names = qnames(visitor, target)
    for n in names:
        if n in TASK_DECORATOR_ROOTS or any(n.startswith(r + ".") for r in TASK_DECORATOR_ROOTS):
            return True
    d = dotted(target) or ""
    return not names and (d == "task" or d.startswith("task."))


def looks_like_operator(visitor, call: cst.Call) -> bool:
    name = callee_name(call) or ""
    if name.endswith(("Operator", "Sensor")) or name in {"GenericTransfer"}:
        return True
    if name in ("partial", "expand", "expand_kwargs") and isinstance(call.func, cst.Attribute):
        inner = call.func.value
        if isinstance(inner, cst.Call):
            return looks_like_operator(visitor, inner)
        n = dotted(inner) or ""
        return n.endswith(("Operator", "Sensor"))
    return is_task_decorator(visitor, call)


def string_value(node: cst.BaseExpression) -> str | None:
    if isinstance(node, cst.SimpleString):
        try:
            return node.evaluated_value if isinstance(node.evaluated_value, str) else None
        except Exception:
            return None
    if isinstance(node, cst.ConcatenatedString):
        left, right = string_value(node.left), string_value(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def remove_arg(call: cst.Call, arg: cst.Arg) -> cst.Call:
    args = list(call.args)
    idx = next(i for i, a in enumerate(args) if a is arg)
    removed = args.pop(idx)
    if args and idx == len(args):
        # Removed the last arg: the new last arg inherits its closing comma/whitespace.
        args[-1] = args[-1].with_changes(comma=removed.comma, whitespace_after_arg=removed.whitespace_after_arg)
    return call.with_changes(args=args)


def rename_kwarg(arg: cst.Arg, new: str) -> cst.Arg:
    return arg.with_changes(keyword=cst.Name(new))


def _separator(call: cst.Call):
    for a in call.args[:-1]:
        if isinstance(a.comma, cst.Comma):
            return a.comma.whitespace_after
    ws = call.whitespace_before_args
    if isinstance(ws, cst.ParenthesizedWhitespace):
        return ws
    return cst.SimpleWhitespace(" ")


def add_kwarg(call: cst.Call, name: str, value: cst.BaseExpression) -> cst.Call:
    new = cst.Arg(
        keyword=cst.Name(name),
        value=value,
        equal=cst.AssignEqual(
            whitespace_before=cst.SimpleWhitespace(""), whitespace_after=cst.SimpleWhitespace("")
        ),
    )
    args = list(call.args)
    if not args:
        return call.with_changes(args=[new])
    last = args[-1]
    new = new.with_changes(comma=last.comma, whitespace_after_arg=last.whitespace_after_arg)
    args[-1] = last.with_changes(
        comma=cst.Comma(whitespace_after=_separator(call)), whitespace_after_arg=cst.SimpleWhitespace("")
    )
    args.append(new)
    return call.with_changes(args=args)


def is_docstring(node: cst.SimpleStatementLine, parent_body_index: int) -> bool:
    return parent_body_index == 0 and m.matches(node, m.SimpleStatementLine(body=[m.Expr(m.SimpleString())]))
