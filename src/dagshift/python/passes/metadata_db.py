"""Direct metadata-database access, which Airflow 3 blocks for task code.

DS401  ORM sessions / models used directly (provide_session, create_session, session.query,
       DagRun.find, XCom.get_one, DagBag(...))
DS402  DB-backed methods on runtime objects (dag_run.get_task_instances(), ti.current_state(), ...)
"""
from __future__ import annotations

import libcst as cst
from libcst.metadata import PositionProvider, QualifiedNameProvider

from ...findings import Severity
from ..base import Pass
from ..helpers import qnames

_ORM = {"DagRun", "TaskInstance", "XCom", "DagModel", "Log", "SlaMiss", "DagTag", "TaskFail",
        "TaskReschedule", "RenderedTaskInstanceFields", "Trigger", "DagWarning", "BaseXCom", "Pool"}
_SESSION = {
    "airflow.utils.session.provide_session", "airflow.utils.session.create_session",
    "airflow.utils.db.provide_session", "airflow.utils.db.create_session",
    "airflow.settings.Session", "airflow.utils.session.NEW_SESSION",
}
_SESSION_METHODS = {"query", "execute", "scalar", "scalars", "add", "merge", "delete", "commit", "flush",
                    "add_all", "get"}
_ORM_CLASSMETHODS = {"find", "find_duplicate", "get_one", "get_many", "get_value", "set", "clear", "get_current",
                     "get_paused_dag_ids", "get_dagmodel", "deactivate_deleted_dags"}
_RUNTIME_RECEIVERS = {"dag_run", "dagrun", "dr", "ti", "task_instance", "dag", "run"}
_DB_METHODS = {
    "get_task_instances": "task_states",
    "get_task_instance": "task_states",
    "current_state": "task_states",
    "refresh_from_db": "task_states",
    "set_state": "none",
    "update_state": "none",
    "get_dagrun": "dagrun",
    "get_last_dagrun": "dagrun",
    "get_previous_scheduled_dagrun": "dagrun",
    "clear": "none",
    "get_num_active_runs": "dagrun",
    "get_active_runs": "dagrun",
}


def _orm_name(names: set[str]) -> str | None:
    for n in names:
        if n.startswith("airflow.models.") and n.rsplit(".", 1)[-1] in _ORM:
            return n.rsplit(".", 1)[-1]
    return None


class MetadataDbPass(Pass):
    DESCRIPTION = "Direct metadata DB access from DAG/task code"
    REQUIRES_ANY = tuple(_ORM | set(_DB_METHODS) | {"session", "DagBag"})
    METADATA_DEPENDENCIES = (PositionProvider, QualifiedNameProvider)

    def __init__(self, context, state) -> None:
        super().__init__(context, state)
        self.active = self.kb.target_at_least("3.0")
        self._stack: list[dict] = []
        self._annotation_depth = 0

    def report(self, rule_id, severity, node, message, recipe=None):  # noqa: D401
        if self.active:
            super().report(rule_id, severity, node, message, recipe)

    # -- function bookkeeping ----------------------------------------------------------
    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        rec = {"node": node, "session": False, "models": set(), "ops": set()}
        for d in node.decorators:
            target = d.decorator.func if isinstance(d.decorator, cst.Call) else d.decorator
            if qnames(self, target) & _SESSION:
                rec["session"] = True
        for p in [*node.params.params, *node.params.kwonly_params]:
            if p.name.value == "session" and p.default is not None:
                rec["session"] = True
        self._stack.append(rec)

    def leave_FunctionDef(self, original_node, updated_node):
        rec = self._stack.pop()
        if rec["session"] or rec["models"]:
            self._emit(rec)
        return updated_node

    def visit_Annotation(self, node) -> None:
        self._annotation_depth += 1

    def leave_Annotation(self, original_node, updated_node):
        self._annotation_depth -= 1
        return updated_node

    def _current(self) -> dict | None:
        return self._stack[-1] if self._stack else None

    def _mark(self, node: cst.CSTNode, kind: str, value: str | None = None) -> None:
        rec = self._current()
        if kind == "op":
            if rec is not None and value:
                rec["ops"].add(value)
            return
        if rec is None:
            # Module level: runs while the DAG file is parsed.
            self.report("DS401", Severity.MANUAL, node,
                        f"Module-level metadata DB access ({value or kind}) runs during DAG parsing; "
                        "Airflow 3 DAG files cannot reach the metadata DB.",
                        _recipe({value} if value else set(), set(), self.kb))
            return
        if kind == "session":
            rec["session"] = True
        elif kind == "model" and value:
            rec["models"].add(value)

    def _emit(self, rec: dict) -> None:
        fn = rec["node"]
        what = ", ".join(sorted(rec["models"])) or "an ORM session"
        self.report("DS401", Severity.MANUAL, fn,
                    f"`{fn.name.value}` reads or writes the metadata DB directly ({what}). Airflow 3 blocks "
                    "database access from task code; this fails at run time, not at parse time.",
                    _recipe(rec["models"], rec["ops"], self.kb))

    # -- detection (post-order, so chained calls see their inner calls first) --------
    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call):
        node = original_node
        f = node.func
        names = qnames(self, f)
        if names & _SESSION:
            self._mark(node, "session")
        if any(n.endswith(".DagBag") for n in names):
            self.report("DS401", Severity.MANUAL, node,
                        "Building a DagBag inside DAG or task code is not supported in Airflow 3.",
                        "Use the REST API v2 (apache-airflow-client) to inspect DAGs, or move this logic "
                        "out of the DAG folder.")
        model = _orm_name(names)
        if model and not isinstance(f, cst.Attribute):
            self._mark(node, "model", model)  # TaskInstance(...) constructed directly
        if isinstance(f, cst.Attribute):
            meth = f.attr.value
            recv = f.value
            if isinstance(recv, cst.Name) and recv.value == "session" and meth in _SESSION_METHODS:
                self._mark(node, "session")
                for a in node.args:
                    m2 = _orm_name(qnames(self, a.value))
                    if m2:
                        self._mark(node, "model", m2)
            if meth == "delete":
                self._mark(node, "op", "delete")
            recv_model = _orm_name(qnames(self, recv))
            if recv_model and meth in _ORM_CLASSMETHODS:
                self._mark(node, "model", recv_model)
            self._runtime_method(node, recv, meth)
        return updated_node

    def _runtime_method(self, node: cst.Call, recv: cst.BaseExpression, meth: str) -> None:
        if meth not in _DB_METHODS or not self.enabled("DS402"):
            return
        root = None
        if isinstance(recv, cst.Name):
            root = recv.value
        elif isinstance(recv, cst.Subscript) and isinstance(recv.value, cst.Name) and len(recv.slice) == 1:
            el = recv.slice[0].slice
            if isinstance(el, cst.Index) and isinstance(el.value, cst.SimpleString):
                root = el.value.evaluated_value
        if root not in _RUNTIME_RECEIVERS:
            return
        if meth == "clear" and root not in ("dag",):
            return
        kind = _DB_METHODS[meth]
        sigs = self.kb.runtime_ti_signatures
        if kind == "task_states" and "get_task_states" in sigs:
            recipe = (
                "Ask the Task SDK instead of the ORM. Inside a task:\n"
                "    ti = context['ti']\n"
                "    states = ti.get_task_states(dag_id=ti.dag_id, run_ids=[ti.run_id])[ti.run_id]\n"
                "    failed = [t for t, s in states.items() if s == 'failed' and t != ti.task_id]\n"
                f"Signature on Airflow {self.kb.target}: get_task_states{sigs['get_task_states']}"
            )
        elif kind == "dagrun" and "get_dr_count" in sigs:
            recipe = (
                "Use the Task SDK: context['ti'].get_dagrun_state(dag_id, run_id), "
                "ti.get_dr_count(dag_id=..., states=[...]), or ti.get_previous_dagrun(state=...). "
                "For anything richer, call the REST API v2 with apache-airflow-client."
            )
        else:
            recipe = ("State changes from task code go through the REST API v2 in Airflow 3 "
                      "(apache-airflow-client), not ORM methods.")
        self.report("DS402", Severity.MANUAL, node,
                    f"`{root}.{meth}()` hits the metadata DB, which task code cannot reach in Airflow 3.", recipe)


def _recipe(models: set[str], ops: set[str], kb) -> str:
    lines = []
    if "XCom" in models and "delete" in ops:
        lines.append(
            "XCom cleanup does not belong in a task in Airflow 3. On Cloud Composer 3 use the managed "
            "database retention policy (Terraform: config.data_retention_config."
            "airflow_metadata_retention_config, retention_days 30-730) and delete this DAG. Self-managed: "
            "run `airflow db clean --tables xcom --clean-before-timestamp <ts>` from a maintenance job "
            "that has DB credentials (not from a task)."
        )
    if "DagRun" in models or "DagModel" in models:
        lines.append(
            "Run/DAG queries: use the Task SDK where it covers the need "
            "(context['ti'].get_dr_count(dag_id=..., states=['failed']), get_dagrun_state(dag_id, run_id)), "
            "otherwise the REST API v2 (e.g. GET /api/v2/dags/~/dagRuns?state=failed via "
            "apache-airflow-client). For alerting on failures, platform metrics (Cloud Monitoring on "
            "Composer, CloudWatch on MWAA) avoid custom code entirely."
        )
    if "TaskInstance" in models:
        lines.append(
            "Task-instance state: context['ti'].get_task_states(dag_id=..., run_ids=[...]) or "
            "get_ti_count(dag_id=..., states=[...])."
        )
    if "XCom" in models and "delete" not in ops:
        lines.append("XCom reads/writes: ti.xcom_pull(task_ids=..., key=...) / ti.xcom_push(key, value).")
    if not lines:
        lines.append(
            "Replace session/ORM access with the Task SDK (ti.get_task_states, get_ti_count, get_dr_count, "
            "get_dagrun_state) or the REST API v2 via apache-airflow-client. Querying the metadata DB "
            "with a SQL hook is not supported: the schema is not a public API."
        )
    return "\n".join(lines)
