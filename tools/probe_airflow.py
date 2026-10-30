"""Probe one installed Airflow 3 environment and print facts as JSON.

Run with the *target* environment's interpreter:

    AIRFLOW_HOME=$(mktemp -d) /path/to/venv/bin/python tools/probe_airflow.py facts.json < candidates.json

stdin: {"candidates": {"old.path.Name": "preferred.new.Name" | null}}
stdout: JSON with the Airflow version, airflow.sdk exports, per-path status for old and
new paths, Airflow's own deprecation tables, context keys and key signatures.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import sys
import warnings

os.environ.setdefault("AIRFLOW__CORE__LOAD_EXAMPLES", "False")
os.environ.setdefault("AIRFLOW__CORE__UNIT_TEST_MODE", "False")

_MODULE_WARNINGS: dict[str, list[str]] = {}
_DEPRECATION_NAMES = ("Deprecat", "RemovedIn")


def _is_deprecation(w: warnings.WarningMessage) -> bool:
    if issubclass(w.category, (DeprecationWarning, PendingDeprecationWarning, FutureWarning)):
        return True
    if any(n in w.category.__name__ for n in _DEPRECATION_NAMES):
        return True
    # some modules use a plain UserWarning ("... is deprecated and will be removed ...")
    return issubclass(w.category, UserWarning) and "deprecat" in str(w.message).lower()


def _import_module(mod: str):
    if mod in sys.modules:
        return sys.modules[mod], _MODULE_WARNINGS.get(mod, [])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        m = importlib.import_module(mod)
    msgs = [str(w.message) for w in caught if _is_deprecation(w)]
    _MODULE_WARNINGS[mod] = msgs
    return m, msgs


def resolve(path: str):
    """Return (status, detail, obj). status: ok | deprecated | removed."""
    mod, _, name = path.rpartition(".")
    try:
        m, mod_warnings = _import_module(mod)
    except Exception as exc:  # module gone
        # Maybe "mod" is itself an attribute (e.g. airflow.Dataset where mod=airflow)
        return "removed", f"{type(exc).__name__}: {exc}"[:300], None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            obj = getattr(m, name)
        except Exception as exc:
            try:
                obj = importlib.import_module(path)
            except Exception:
                return "removed", f"{type(exc).__name__}: {exc}"[:300], None
    msgs = mod_warnings + [str(w.message) for w in caught if _is_deprecation(w)]
    if msgs:
        return "deprecated", msgs[0][:300], obj
    return "ok", "", obj


def harvest_deprecation_tables(root: str, package_root: str = "airflow") -> dict[str, str]:
    """Read every add_deprecated_classes({...}, __name__) call in the installed package."""
    out: dict[str, str] = {}
    for dirpath, _dirs, files in os.walk(root):
        if "_vendor" in dirpath or "migrations" in dirpath or "example_dags" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            full = os.path.join(dirpath, fn)
            try:
                src = open(full, encoding="utf-8").read()
            except OSError:
                continue
            if "add_deprecated_classes" not in src:
                continue
            rel = os.path.relpath(full, os.path.dirname(root))[:-3].replace(os.sep, ".")
            module_name = rel[: -len(".__init__")] if rel.endswith(".__init__") else rel
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            assigned: dict[str, ast.AST] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    assigned[node.targets[0].id] = node.value
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "add_deprecated_classes"):
                    continue
                if not node.args:
                    continue
                table = node.args[0]
                if isinstance(table, ast.Name):
                    table = assigned.get(table.id)
                if not isinstance(table, ast.Dict):
                    continue
                for k, v in zip(table.keys, table.values):
                    if isinstance(k, ast.Name) and k.id == "__name__":
                        sub_mod = module_name
                    elif isinstance(k, ast.Constant) and isinstance(k.value, str):
                        sub_mod = f"{module_name}.{k.value}"
                    else:
                        continue
                    if not isinstance(v, ast.Dict):
                        continue
                    for ak, av in zip(v.keys, v.values):
                        if not (isinstance(ak, ast.Constant) and isinstance(av, ast.Constant)):
                            continue
                        attr, target = ak.value, av.value
                        if not (isinstance(attr, str) and isinstance(target, str)):
                            continue
                        if attr == "*":
                            out[f"{sub_mod}.*"] = target
                        elif "." in target:
                            # Airflow resolves targets with target.rsplit(".", 1): always a full path.
                            out[f"{sub_mod}.{attr}"] = target
    return out


def _params(obj) -> list[str]:
    try:
        import attrs  # noqa: PLC0415

        if attrs.has(obj):
            return [getattr(f, "alias", None) or f.name.lstrip("_") for f in attrs.fields(obj) if f.init]
    except Exception:
        pass
    try:
        return [p for p in inspect.signature(obj.__init__ if inspect.isclass(obj) else obj).parameters if p != "self"]
    except Exception:
        return []


def main() -> None:
    payload = json.load(sys.stdin)
    candidates: dict[str, str | None] = payload["candidates"]

    import airflow  # noqa: F401

    try:  # Airflow 3 only
        import airflow.sdk as sdk
    except ImportError:
        sdk = None

    facts: dict = {"airflow_version": airflow.__version__}
    facts["sdk_all"] = sorted(getattr(sdk, "__all__", [])) if sdk else []

    root = os.path.dirname(airflow.__file__)
    tables = harvest_deprecation_tables(root)
    if sdk is not None:
        sdk_root = os.path.dirname(sdk.__file__)
        if not sdk_root.startswith(root):
            tables.update(harvest_deprecation_tables(sdk_root))
    facts["deprecation_tables"] = tables

    paths = dict(candidates)
    for old, new in tables.items():
        if old.endswith(".*"):
            continue
        paths.setdefault(old, new)

    status: dict[str, dict] = {}
    for old in sorted(paths):
        st, detail, obj = resolve(old)
        entry = {"status": st, "detail": detail}
        cands = []
        if paths[old]:
            cands.append(paths[old])
        if old in tables and tables[old] not in cands:
            cands.append(tables[old])
        name = (paths[old] or old).rsplit(".", 1)[-1]
        if name in facts["sdk_all"] and f"airflow.sdk.{name}" not in cands:
            cands.insert(0, f"airflow.sdk.{name}")
        new_checks = []
        for c in cands:
            cst, cdetail, cobj = resolve(c)
            same = None
            if obj is not None and cobj is not None:
                same = (obj is cobj) or (
                    getattr(obj, "__qualname__", None) == getattr(cobj, "__qualname__", object())
                    and getattr(obj, "__module__", None) == getattr(cobj, "__module__", object())
                )
            new_checks.append({"path": c, "status": cst, "detail": cdetail, "same_object": same})
        entry["new"] = new_checks
        status[old] = entry
    facts["paths"] = status

    # Context keys
    try:
        try:
            from airflow.sdk.definitions.context import KNOWN_CONTEXT_KEYS
        except ImportError:  # Airflow 2
            from airflow.utils.context import KNOWN_CONTEXT_KEYS

        facts["context_keys"] = sorted(KNOWN_CONTEXT_KEYS)
    except Exception:
        try:  # 3.0: no KNOWN_CONTEXT_KEYS; the Context TypedDict lists the keys
            from airflow.sdk.definitions.context import Context

            keys: set[str] = set()
            for klass in Context.__mro__:
                keys.update(getattr(klass, "__annotations__", {}))
            facts["context_keys"] = sorted(k for k in keys if not k.startswith("_"))
        except Exception as exc:
            facts["context_keys_error"] = str(exc)

    try:
        from airflow.sdk import DAG, BaseOperator, Variable
    except ImportError:  # Airflow 2
        from airflow.models.baseoperator import BaseOperator
        from airflow.models.dag import DAG
        from airflow.models.variable import Variable

    facts["dag_params"] = _params(DAG)
    try:
        sig = inspect.signature(DAG.__init__)
        facts["dag_positional"] = [
            p.name for p in sig.parameters.values()
            if p.name != "self" and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
    except Exception as exc:
        facts["dag_positional_error"] = str(exc)
    facts["baseoperator_params"] = _params(BaseOperator)
    facts["variable_get_params"] = list(inspect.signature(Variable.get).parameters)
    try:
        try:
            from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator
        except ImportError:
            from airflow.operators.trigger_dagrun import TriggerDagRunOperator

        facts["trigger_dagrun_params"] = _params(TriggerDagRunOperator)
    except Exception as exc:
        facts["trigger_dagrun_params_error"] = str(exc)
    try:
        try:
            from airflow.providers.standard.operators.python import PythonOperator
        except ImportError:
            from airflow.operators.python import PythonOperator

        facts["python_operator_params"] = _params(PythonOperator)
    except Exception as exc:
        facts["python_operator_params_error"] = str(exc)
    try:
        from airflow.plugins_manager import AirflowPlugin

        facts["plugin_attrs"] = sorted(k for k in vars(AirflowPlugin) if not k.startswith("_"))
    except Exception as exc:
        facts["plugin_attrs_error"] = str(exc)
    try:
        from airflow.sdk.execution_time.task_runner import RuntimeTaskInstance

        facts["runtime_ti_methods"] = sorted(
            m for m in dir(RuntimeTaskInstance) if not m.startswith("_") and not m.startswith("model_")
        )
        sigs = {}
        for m in ("get_task_states", "get_ti_count", "get_dr_count", "get_dagrun_state", "get_previous_dagrun"):
            if hasattr(RuntimeTaskInstance, m):
                try:
                    sigs[m] = str(inspect.signature(getattr(RuntimeTaskInstance, m)))
                except Exception:
                    pass
        facts["runtime_ti_signatures"] = sigs
    except Exception as exc:
        facts["runtime_ti_methods_error"] = str(exc)
    try:
        from airflow.configuration import conf

        facts["config_defaults"] = {
            "scheduler.create_cron_data_intervals": conf.get("scheduler", "create_cron_data_intervals", fallback=None),
            "scheduler.catchup_by_default": conf.get("scheduler", "catchup_by_default", fallback=None),
            "core.dag_ignore_file_syntax": conf.get("core", "dag_ignore_file_syntax", fallback=None),
            "scheduler.create_delta_data_intervals": conf.get("scheduler", "create_delta_data_intervals", fallback=None),
        }
    except Exception as exc:
        facts["config_defaults_error"] = str(exc)

    try:
        import airflow.providers.standard as std

        facts["providers_standard_version"] = getattr(std, "__version__", None)
    except Exception:
        pass

    # Airflow logs to stdout on import, so facts go to a file.
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(facts, fh, indent=1, sort_keys=True, default=str)
    else:
        json.dump(facts, sys.stdout, indent=1, sort_keys=True, default=str)


if __name__ == "__main__":
    main()
