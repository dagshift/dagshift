"""Code that still imports on Airflow 3 but behaves differently.

DS501  try/except ImportError around Airflow imports (a failed import silently swaps in fallbacks)
DS502  values computed at parse time (now(), time(), uuid4()) in DAG/operator arguments
DS503  cron schedules: Airflow 3 uses CronTriggerTimetable, so logical_date/ds/data_interval_* shift
DS504  plugin features: Flask/FAB views and removed plugin attributes
"""
from __future__ import annotations

import re

import libcst as cst
from libcst.metadata import ParentNodeProvider, PositionProvider, QualifiedNameProvider

from ...findings import Severity
from ..base import Pass
from ..helpers import dotted, is_dag_call, kwarg, looks_like_operator, qnames, string_value

_DYNAMIC = {
    "datetime.datetime.now", "datetime.datetime.utcnow", "datetime.datetime.today", "datetime.date.today",
    "time.time", "time.time_ns", "pendulum.now", "pendulum.today", "pendulum.yesterday", "pendulum.tomorrow",
    "airflow.utils.timezone.utcnow", "airflow.sdk.timezone.utcnow", "uuid.uuid4", "uuid.uuid1",
    "random.random", "random.randint", "random.choice", "os.urandom",
}
_PRESETS = {"@hourly", "@daily", "@weekly", "@monthly", "@quarterly", "@yearly", "@annually", "@midnight"}
_CRON = re.compile(r"^\s*(\S+\s+){4}\S+(\s+\S+)?\s*$")
_FAB_ATTRS = {"flask_blueprints", "appbuilder_views", "appbuilder_menu_items", "admin_views", "menu_links"}


def _is_cron(value: str) -> bool:
    v = value.strip()
    return v in _PRESETS or bool(_CRON.match(v))


class SafetyPass(Pass):
    DESCRIPTION = "Behaviour changes that do not fail at import time"
    METADATA_DEPENDENCIES = (PositionProvider, QualifiedNameProvider, ParentNodeProvider)

    def __init__(self, context, state) -> None:
        super().__init__(context, state)
        self._default_args_dicts: dict[str, cst.Dict] = {}

    # -- DS501 --------------------------------------------------------------------------
    def visit_Try(self, node: cst.Try) -> None:
        if not self.enabled("DS501"):
            return
        catches = False
        for h in node.handlers:
            names = set()
            if h.type is None:
                catches = True
            else:
                exprs = h.type.elements if isinstance(h.type, cst.Tuple) else [h.type]
                for e in exprs:
                    names.add(dotted(e.value if isinstance(e, cst.Element) else e) or "")
                if names & {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}:
                    catches = True
        if not catches:
            return
        airflow_imports = []
        for stmt in node.body.body:
            if isinstance(stmt, cst.SimpleStatementLine):
                for small in stmt.body:
                    if isinstance(small, cst.ImportFrom) and small.module is not None:
                        mod = dotted(small.module) or ""
                        if mod.split(".")[0] == "airflow":
                            airflow_imports.append(mod)
                    elif isinstance(small, cst.Import):
                        for a in small.names:
                            if (dotted(a.name) or "").split(".")[0] == "airflow":
                                airflow_imports.append(dotted(a.name))
        if airflow_imports:
            self.report(
                "DS501", Severity.WARN, node,
                "Airflow imports are wrapped in try/except: if one of them is missing on Airflow 3 the "
                "except branch runs silently and the DAG imports with fallback objects (often a no-op "
                "decorator), failing later at run time instead of at parse time.",
                "Guard only the 'is Airflow installed' check (e.g. `import airflow` alone inside the try), "
                "or catch ImportError and re-raise when running under Airflow. dagshift has rewritten the "
                "imports inside the block; run `dagshift validate` to confirm they resolve.",
            )

    # -- DS502 --------------------------------------------------------------------------
    def visit_Assign(self, node: cst.Assign) -> None:
        if isinstance(node.value, cst.Dict):
            for t in node.targets:
                if isinstance(t.target, cst.Name) and "args" in t.target.value.lower():
                    self._default_args_dicts[t.target.value] = node.value

    def _in_parse_time_args(self, node: cst.CSTNode) -> bool:
        cur = node
        while True:
            try:
                parent = self.get_metadata(ParentNodeProvider, cur)
            except Exception:
                return False
            if isinstance(parent, (cst.FunctionDef, cst.Lambda)):
                return False
            if isinstance(parent, cst.Call) and cur is not parent.func:
                if is_dag_call(self, parent) or looks_like_operator(self, parent):
                    return True
            if isinstance(parent, cst.Assign) and isinstance(parent.value, cst.Dict):
                for t in parent.targets:
                    if isinstance(t.target, cst.Name) and "args" in t.target.value.lower():
                        return True
            if isinstance(parent, cst.Module):
                return False
            cur = parent

    def visit_Call(self, node: cst.Call) -> None:
        names = qnames(self, node.func)
        if names & _DYNAMIC and self.enabled("DS502") and self.kb.target_at_least("3.0") and self._in_parse_time_args(node):
            self.report(
                "DS502", Severity.WARN, node,
                f"`{sorted(names & _DYNAMIC)[0]}()` is evaluated every time the DAG file is parsed. "
                "Airflow 3 versions DAGs, so a value that changes on each parse creates a new DAG version "
                "each time (and in Airflow 2 it was already non-deterministic).",
                "Use a fixed value, or compute it at run time: in a templated field "
                "('{{ data_interval_end }}', '{{ macros.datetime.now() }}') or inside the task callable.",
            )
        if is_dag_call(self, node):
            self._check_schedule(node)

    # -- DS503 --------------------------------------------------------------------------
    def _check_schedule(self, call: cst.Call) -> None:
        if not self.enabled("DS503") or self.options.keep_cron_data_intervals or not self.kb.crosses("3.0"):
            return  # with keep_cron_data_intervals the CallsPass rewrites the schedule instead
        arg = kwarg(call, "schedule") or kwarg(call, "schedule_interval")
        if arg is None:
            return
        value = string_value(arg.value)
        kind = None
        if value is not None and _is_cron(value):
            kind = "cron"
        elif isinstance(arg.value, cst.Call) and qnames(self, arg.value.func) & {"datetime.timedelta", "pendulum.duration"}:
            kind = "delta"
        elif isinstance(arg.value, cst.Name) and self.state.masked.is_placeholder(arg.value.value):
            self.report("DS503", Severity.INFO, arg,
                        "Schedule is injected by a template. If the rendered value is a cron string or timedelta, "
                        "Airflow 3 runs it with a trigger timetable (see DS503 for what changes).")
            return
        if kind is None:
            return
        sensitive = self.state.data_interval_sensitive
        timetable, cfg = (("CronTriggerTimetable", "create_cron_data_intervals") if kind == "cron"
                          else ("DeltaTriggerTimetable", "create_delta_data_intervals"))
        label = repr(value) if kind == "cron" else "timedelta"
        self.report(
            "DS503", Severity.WARN if sensitive else Severity.INFO, arg,
            f"Schedule {label}: Airflow 3 runs it with {timetable}, so a run's logical_date is the trigger "
            "time instead of the start of the interval, and data_interval_start == data_interval_end."
            + (" This file reads ds/ts/logical_date/data_interval values, which shift by one period."
               if sensitive else " Nothing in this file reads those values."),
            "To keep Airflow 2 semantics: run dagshift with keep_cron_data_intervals=true (wraps the schedule "
            f"in a *DataIntervalTimetable), or set [scheduler] {cfg} = True for the whole environment "
            f"(Cloud Composer: airflow_config_overrides 'scheduler-{cfg}' = 'True') before the first "
            "Airflow 3 run.",
        )

    # -- DS504 --------------------------------------------------------------------------
    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        if not self.enabled("DS504"):
            return
        is_plugin = False
        for b in node.bases:
            names = qnames(self, b.value)
            if any(n.endswith("AirflowPlugin") for n in names) or (dotted(b.value) or "").endswith("AirflowPlugin"):
                is_plugin = True
        if not is_plugin or not isinstance(node.body, cst.IndentedBlock):
            return
        target_attrs = self.kb.plugin_attrs
        for stmt in node.body.body:
            if not isinstance(stmt, cst.SimpleStatementLine):
                continue
            for small in stmt.body:
                if not isinstance(small, (cst.Assign, cst.AnnAssign)):
                    continue
                targets = [t.target for t in small.targets] if isinstance(small, cst.Assign) else [small.target]
                for t in targets:
                    if not isinstance(t, cst.Name):
                        continue
                    attr = t.value
                    if attr in _FAB_ATTRS and self.kb.target_at_least("3.0"):
                        ui = "fastapi_apps" + (", external_views and react_apps" if "react_apps" in target_attrs else "")
                        self.report(
                            "DS504", Severity.MANUAL, small,
                            f"Plugin attribute `{attr}` is a Flask/FAB UI extension. The Airflow 3 UI does not "
                            "render it; it only works with the FAB provider's legacy views.",
                            f"Port it to {ui} on Airflow {self.kb.target}, or install "
                            "apache-airflow-providers-fab and use FabAuthManager to keep the legacy view.",
                        )
                    elif attr in self.kb.af2["plugin_attrs"] and attr not in target_attrs:
                        self.report(
                            "DS504", Severity.MANUAL, small,
                            f"Plugin attribute `{attr}` is not supported in Airflow {self.kb.target}.",
                            "Import operators/hooks/sensors as regular Python modules; executors are configured, "
                            "not registered through plugins.",
                        )
