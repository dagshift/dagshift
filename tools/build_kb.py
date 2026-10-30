"""Build src/dagshift/_data/kb.json from probe results (one probe per Airflow minor, 2.x and 3.x).

Usage:
    python tools/build_kb.py 2.7=/path/af-2.7.3/bin/python ... 3.3=/path/af-3.3.1/bin/python

For each target interpreter this runs tools/probe_airflow.py, then folds the facts into
one versioned knowledge base. Re-run it when a new Airflow minor ships.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from candidates import CANDIDATES, NOTES  # noqa: E402

# Airflow 2's own legacy-path tables, with provider targets verified against current provider
# wheels (see tools/harvest_af2.py). Optional: the KB builds without it.
AF2_MOVES: dict = {}
if (HERE / "af2_moves.json").exists():
    AF2_MOVES = json.loads((HERE / "af2_moves.json").read_text())


def all_candidates() -> dict[str, str | None]:
    merged: dict[str, str | None] = {}
    for old, e in AF2_MOVES.items():
        if e["kind"] == "provider":
            merged[old] = e["af2_target"] if e.get("exists") else None
        else:
            merged[old] = e["af2_target"]
    merged.update(CANDIDATES)  # curated entries win
    return merged

# Airflow 2.11 surface used to compute what disappeared (not probed: Airflow 2 is the source).
AF2_CONTEXT_KEYS = [
    "conf", "conn", "dag", "dag_run", "data_interval_end", "data_interval_start", "ds", "ds_nodash",
    "exception", "execution_date", "expanded_ti_count", "inlet_events", "inlets", "logical_date",
    "macros", "map_index_template", "next_ds", "next_ds_nodash", "next_execution_date", "outlet_events",
    "outlets", "params", "prev_data_interval_end_success", "prev_data_interval_start_success",
    "prev_ds", "prev_ds_nodash", "prev_end_date_success", "prev_execution_date",
    "prev_execution_date_success", "prev_start_date_success", "reason", "run_id", "task",
    "task_instance", "task_instance_key_str", "templates_dict", "test_mode", "ti", "tomorrow_ds",
    "tomorrow_ds_nodash", "triggering_dataset_events", "ts", "ts_nodash", "ts_nodash_with_tz", "var",
    "yesterday_ds", "yesterday_ds_nodash",
]
AF2_DAG_PARAMS = (
    "dag_id description schedule schedule_interval timetable start_date end_date full_filepath "
    "template_searchpath template_undefined user_defined_macros user_defined_filters default_args "
    "concurrency max_active_tasks max_active_runs max_consecutive_failed_dag_runs dagrun_timeout "
    "sla_miss_callback default_view orientation catchup on_success_callback on_failure_callback doc_md "
    "params access_control is_paused_upon_creation jinja_environment_kwargs "
    "render_template_as_native_obj tags owner_links auto_register fail_stop dag_display_name"
).split()
AF2_OPERATOR_PARAMS = (
    "task_id owner email email_on_retry email_on_failure retries retry_delay retry_exponential_backoff "
    "max_retry_delay start_date end_date depends_on_past ignore_first_depends_on_past "
    "wait_for_past_depends_before_skipping wait_for_downstream dag params default_args priority_weight "
    "weight_rule queue pool pool_slots sla execution_timeout on_execute_callback on_failure_callback "
    "on_success_callback on_retry_callback on_skipped_callback pre_execute post_execute trigger_rule "
    "resources run_as_user task_concurrency map_index_template max_active_tis_per_dag "
    "max_active_tis_per_dagrun executor executor_config do_xcom_push multiple_outputs inlets outlets "
    "task_group doc doc_md doc_json doc_yaml doc_rst task_display_name logger_name allow_nested_operators"
).split()
AF2_TRIGGER_DAGRUN_PARAMS = [
    "trigger_dag_id", "trigger_run_id", "conf", "execution_date", "logical_date", "reset_dag_run",
    "wait_for_completion", "poke_interval", "allowed_states", "failed_states",
    "skip_when_already_exists", "deferrable",
]
AF2_PLUGIN_ATTRS = [
    "name", "source", "hooks", "executors", "macros", "admin_views", "flask_blueprints", "menu_links",
    "appbuilder_views", "appbuilder_menu_items", "global_operator_extra_links", "operator_extra_links",
    "timetables", "listeners", "ti_deps", "priority_weight_strategies", "operators", "sensors",
]

_INTERNAL_MARKERS = ("._internal", ".execution_time.", "._shared.", ".definitions.")


def run_probe(python: str) -> dict:
    with tempfile.TemporaryDirectory() as home, tempfile.NamedTemporaryFile("r", suffix=".json") as out:
        env = dict(os.environ, AIRFLOW_HOME=home)
        proc = subprocess.run(
            [python, "-I", str(HERE / "probe_airflow.py"), out.name],
            input=json.dumps({"candidates": all_candidates()}),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        if proc.returncode != 0:
            raise SystemExit(f"probe failed for {python}:\n{proc.stderr[-3000:]}")
        return json.load(open(out.name))


def pick_new(old: str, entry: dict, curated: str | None, explicit_none: bool) -> tuple[str | None, bool]:
    """Choose the replacement path for one target version. Returns (path, internal)."""
    if explicit_none:
        return None, False
    ok = [n for n in entry["new"] if n["status"] == "ok"]
    if not ok:
        return None, False
    old_removed = entry["status"] == "removed"

    def internal(p: str) -> bool:
        return any(m in p for m in _INTERNAL_MARKERS)

    # 1. airflow.sdk export that is the same object (or the curated path itself)
    for n in ok:
        if n["path"].startswith("airflow.sdk.") and n["path"].count(".") == 2:
            if n["same_object"] or n["path"] == curated or (old_removed and curated and curated.endswith(
                "." + n["path"].rsplit(".", 1)[-1]
            )):
                return n["path"], False
    # 2. curated path
    for n in ok:
        if n["path"] == curated:
            return n["path"], internal(n["path"])
    # 3. any public candidate that is the same object
    for n in ok:
        if not internal(n["path"]) and (n["same_object"] or old_removed):
            return n["path"], False
    # 4. internal path from Airflow's own table (last resort)
    for n in ok:
        if n["same_object"] or old_removed:
            return n["path"], True
    return None, False


def main(argv: list[str]) -> None:
    targets: dict[str, str] = {}
    for arg in argv:
        tgt, _, py = arg.partition("=")
        targets[tgt] = py
    facts = {t: run_probe(py) for t, py in sorted(targets.items())}

    kb: dict = {
        "schema": 1,
        "built_from": {t: f["airflow_version"] for t, f in facts.items()},
        "targets": sorted(facts, key=lambda v: tuple(int(x) for x in v.split("."))),
        "notes": NOTES,
        "symbols": {},
        "module_moves": {},
        "sdk_all": {t: f["sdk_all"] for t, f in facts.items()},
        "context_keys": {t: f.get("context_keys", []) for t, f in facts.items()},
        "removed_context_keys": {},
        "dag_params": {t: f["dag_params"] for t, f in facts.items()},
        "operator_params": {t: f["baseoperator_params"] for t, f in facts.items()},
        "trigger_dagrun_params": {t: f.get("trigger_dagrun_params", []) for t, f in facts.items()},
        "python_operator_params": {t: f.get("python_operator_params", []) for t, f in facts.items()},
        "plugin_attrs": {t: f.get("plugin_attrs", []) for t, f in facts.items()},
        "runtime_ti_methods": {t: f.get("runtime_ti_methods", []) for t, f in facts.items()},
        "runtime_ti_signatures": {t: f.get("runtime_ti_signatures", {}) for t, f in facts.items()},
        "dag_positional": {t: f.get("dag_positional", ["dag_id"]) for t, f in facts.items()},
        "variable_get_params": {t: f["variable_get_params"] for t, f in facts.items()},
        "config_defaults": {t: f.get("config_defaults", {}) for t, f in facts.items()},
        "providers_standard_version": {t: f.get("providers_standard_version") for t, f in facts.items()},
        "af2": {
            "context_keys": AF2_CONTEXT_KEYS,
            "dag_params": AF2_DAG_PARAMS,
            "operator_params": AF2_OPERATOR_PARAMS,
            "trigger_dagrun_params": AF2_TRIGGER_DAGRUN_PARAMS,
            "plugin_attrs": AF2_PLUGIN_ATTRS,
        },
    }
    for t, f in facts.items():
        kb["removed_context_keys"][t] = sorted(set(AF2_CONTEXT_KEYS) - set(f.get("context_keys", [])))
        for k, target in f["deprecation_tables"].items():
            if k.endswith(".*"):
                kb["module_moves"].setdefault(k[:-2], {})[t] = target

    cands = all_candidates()
    all_paths = sorted({p for f in facts.values() for p in f["paths"]})
    for old in all_paths:
        per: dict[str, list] = {}
        af2 = AF2_MOVES.get(old) if old not in CANDIDATES else None
        for t, f in facts.items():
            entry = f["paths"].get(old)
            if entry is None:
                continue
            explicit_none = old in cands and cands[old] is None
            new, internal = pick_new(old, entry, cands.get(old), explicit_none)
            if af2 and af2["kind"] == "provider" and af2.get("exists") and new is None:
                # provider target verified in the provider's current wheel (providers are not
                # installed in the probe environments)
                new, internal = af2["af2_target"], False
            per[t] = [entry["status"], new, internal]
        kb["symbols"][old] = per
        if af2 and af2.get("note") and old not in kb["notes"]:
            kb["notes"][old] = af2["note"]

    # Chain core legacy paths through their Airflow 2 target (bash_operator -> bash -> standard).
    for old, e in AF2_MOVES.items():
        if e["kind"] != "core" or old not in kb["symbols"]:
            continue
        if old in CANDIDATES and CANDIDATES[old] is None:
            continue  # curated as "no replacement"
        target = e["af2_target"]
        for t, val in kb["symbols"][old].items():
            if val[1] is not None:
                continue
            nxt = kb["symbols"].get(target, {}).get(t)
            if nxt and nxt[1]:
                val[1], val[2] = nxt[1], nxt[2]
            elif nxt and nxt[0] == "ok":
                val[1] = target
    kb["provider_versions"] = {e["package"]: e.get("version") for e in AF2_MOVES.values()
                               if e.get("kind") == "provider" and e.get("version")}

    out = ROOT / "src" / "dagshift" / "_data" / "kb.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(kb, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(kb['symbols'])} symbols, targets {kb['targets']})")


if __name__ == "__main__":
    main(sys.argv[1:])
