"""Config files that drive DAG generation: YAML, JSON and non-Python templates.

Every key rule asks the knowledge base whether the target Airflow still accepts the key.

Edits are line/regex based so comments, ordering and quoting survive untouched.

DS701  dotted Airflow import paths in config values (operator: airflow.operators.bash.BashOperator)
DS301/DS303/DS304  Airflow templates inside config values (same rules as Python strings)
DS703  dag-factory keys (schedule_interval, concurrency, task_concurrency, provide_context, ...)
DS704  removed Airflow parameter names used as keys in a homegrown config format
DS206  sla keys in dag-factory configs
DS807  dag-factory: point to its own `dagfactory convert` / `dagfactory lint`
"""
from __future__ import annotations

import re

from .findings import FileResult, Finding, Severity
from .jinja_text import ContextPolicy, find_dotted_paths, rewrite_jinja
from .kb import KnowledgeBase
from .options import Options

_DAG_FACTORY = re.compile(
    r"^\s*-?\s*operator\s*:\s*['\"]?(airflow\.|[\w.]+Operator|[\w.]+Sensor)|^\s*task_groups?\s*:", re.M
)
_DAG_FACTORY_HINTS = re.compile(r"^\s*(tasks|default_args|default)\s*:", re.M)

# key -> (new key or None to drop, needs_target_check)
DAG_FACTORY_KEYS = {
    "schedule_interval": "schedule",
    "concurrency": "max_active_tasks",
    "task_concurrency": "max_active_tis_per_dag",
    "fail_stop": "fail_fast",
    "provide_context": None,
    "default_view": None,
    "orientation": None,
    "full_filepath": None,
}
# Unambiguous Airflow parameter names worth flagging in any config format.
GENERIC_KEYS = {
    "schedule_interval": "schedule",
    "task_concurrency": "max_active_tis_per_dag",
    "provide_context": None,
    "fail_stop": "fail_fast",
    "default_view": None,
    "orientation": None,
}


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _quote_chooser(text: str, json_mode: bool):
    def choose(offset: int) -> str:
        if json_mode:
            return "'"
        start = text.rfind("\n", 0, offset) + 1
        prefix = text[start:offset]
        if prefix.count('"') % 2 == 1:
            return "'"
        if prefix.count("'") % 2 == 1:
            return '"'
        return "'"

    return choose


def is_dag_factory(text: str) -> bool:
    return bool(_DAG_FACTORY.search(text)) and bool(_DAG_FACTORY_HINTS.search(text))


def migrate_config_text(text: str, path: str, kb: KnowledgeBase, options: Options, kind: str) -> FileResult:
    findings: list[Finding] = []

    def add(rule, sev, line, msg, recipe=None):
        if options.rule_enabled(rule):
            findings.append(Finding(rule, sev, path, line, msg, recipe))

    new = text
    # 1. dotted paths
    out, last = [], 0
    for s, e, dotted in find_dotted_paths(new):
        mv = kb.lookup(dotted)
        if mv and mv.new and mv.new != dotted and (mv.required or (options.modernize and not mv.internal)) \
                and options.rule_enabled("DS701"):
            out.append(new[last:s] + mv.new)
            last = e
            add("DS701", Severity.FIXED, _line_of(new, s), f"`{dotted}` -> `{mv.new}`")
        elif mv and not mv.new and mv.status == "removed":
            add("DS701", Severity.MANUAL, _line_of(new, s),
                f"`{dotted}` does not exist in Airflow {kb.target}.", mv.note)
    out.append(new[last:])
    new = "".join(out)

    # 2. templates
    rewritten, issues = rewrite_jinja(new, quote_for=_quote_chooser(new, kind == "json"),
                                      policy=ContextPolicy.from_kb(kb, options))
    for iss in issues:
        add(iss.rule_id, iss.severity, _line_of(new, iss.offset), iss.message, iss.recipe)
    if options.rule_enabled("DS301"):
        new = rewritten

    # 3. keys
    dag_factory = kind == "yaml" and is_dag_factory(new)
    key_re = (r'^(?P<indent>\s*-?\s*)(?P<q>["\']?)(?P<key>{k})(?P=q)(?P<sep>\s*):'
              if kind == "yaml" else r'(?P<indent>)(?P<q>")(?P<key>{k})(?P=q)(?P<sep>\s*):')
    accepted = kb.dag_params | kb.operator_params
    if dag_factory and kb.target_at_least("3.0"):
        add("DS807", Severity.INFO, 1,
            "dag-factory config. dagshift updated import paths, template variables and renamed keys. "
            "Airflow 3 needs dag-factory 1.0+, which also changed its YAML layout.",
            "Run `dagfactory convert` and then `dagfactory lint` (dag-factory 1.0 CLI) for structural changes "
            "such as tasks/task_groups as lists.")
    keys = dict(DAG_FACTORY_KEYS) if dag_factory else dict(GENERIC_KEYS)
    rename = dag_factory or options.rename_config_keys
    for key, repl in keys.items():
        # What does the target say about this key?
        if repl is None:
            mode = "required" if key not in accepted else None
        elif repl not in accepted:
            mode = None
        elif key not in accepted:
            mode = "required"
        else:
            mode = "modernize" if options.modernize else None
        if mode is None:
            continue
        why = (f"is not accepted in Airflow {kb.target}" if mode == "required"
               else f"is deprecated in Airflow {kb.target}")
        if key == "provide_context":
            why = ("is rejected by Airflow 3" if kb.target_at_least("3.0") else "has had no effect since Airflow 2.0")
        rx = re.compile(key_re.format(k=re.escape(key)), re.M)
        if not rename:
            for mt in rx.finditer(new):
                add("DS704", Severity.WARN if mode == "required" else Severity.INFO, _line_of(new, mt.start()),
                    f"Config key `{key}` matches an Airflow parameter that {why}"
                    + (f" (now `{repl}`)." if repl else "."),
                    "If your generator passes this key straight into DAG()/an operator (e.g. **config), "
                    "rename it (or run with rename_config_keys=true). If the generator maps it in code, "
                    "dagshift has already fixed the Python side.")
            continue
        if repl is None:
            # Drop "key: value" lines (not list-item starts, where dropping would break the item).
            drop_rx = re.compile(key_re.format(k=re.escape(key)) + r"[^\n]*\n?", re.M)

            def _drop(mt, key=key, src=new, why=why):
                if "-" in mt.group("indent"):
                    add("DS703", Severity.MANUAL, _line_of(src, mt.start()),
                        f"Remove `{key}` ({why}); it starts a list item, so dagshift left it.")
                    return mt.group(0)
                add("DS703", Severity.FIXED, _line_of(src, mt.start()), f"Removed `{key}` ({why}).")
                return ""

            new = drop_rx.sub(_drop, new)
        else:
            def _sub(mt, repl=repl, key=key, src=new, why=why):
                add("DS703", Severity.FIXED, _line_of(src, mt.start()), f"`{key}` {why}; renamed to `{repl}`.")
                return f"{mt.group('indent')}{mt.group('q')}{repl}{mt.group('q')}{mt.group('sep')}:"

            new = rx.sub(_sub, new)
    if dag_factory and kb.target_at_least("3.0"):
        for mt in re.finditer(r"^\s*(sla|sla_miss_callback)\s*:", new, re.M):
            add("DS206", Severity.WARN, _line_of(new, mt.start()),
                f"`{mt.group(1)}` has no effect in Airflow 3: SLAs were removed (Deadline Alerts replace them).")
    findings.sort(key=lambda f: (f.line, f.rule_id))
    return FileResult(path=path, original=text, migrated=new, findings=findings)
