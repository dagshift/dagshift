# How it works

dagshift is deterministic: the same files and versions always give the same result. There is no model
guessing at your code. Every rewrite comes from facts measured on real Airflow installs.

## The knowledge base is measured, not typed

For every supported release (2.7.3, 2.8.4, 2.9.3, 2.10.5, 2.11.0, 3.0.6, 3.1.8, 3.2.2, 3.3.1), dagshift's build
installs Airflow, imports every legacy path DAG code uses (plus Airflow 2's own deprecated-path tables), and
records whether the old path works, is deprecated or is removed, and which replacement imports **and is the
same object**. It also records the DAG, operator and `Variable.get` parameters, context keys and plugin
attributes each version accepts. Provider targets are checked against current provider wheels.

The result, 860 paths across 9 targets, ships inside the extension.

## Semantics first

A rewrite happens only when the new code means the same thing:

* `days_ago(2)` becomes the fixed date it returned on the day you upgrade (configurable).
* `{{ next_ds }}` becomes `{{ data_interval_end | ds }}`. `{{ prev_ds }}` has no equivalent, so it is
  reported instead of rewritten.
* A DAG without `schedule` gets `schedule=timedelta(days=1)` when the upgrade crosses into Airflow 3, because
  Airflow 2 ran it daily and Airflow 3 would never run it.
* Cron schedules keep their string, and files that read `ds` or `logical_date` are flagged, because Airflow 3
  runs cron with `CronTriggerTimetable`. Opt into `CronDataIntervalTimetable` to keep Airflow 2 semantics.

## It proves the output

Validation loads the result with the target Airflow's own DagBag. The project's own test suite does the same
against real Airflow 2 and 3 installs on every push to `main`.

## Idempotent

Upgrading already-upgraded code changes nothing.

## Templated files

Jinja-templated DAG files (`{% for %}` in Python positions) are not valid Python, so most tools cannot parse
them. dagshift replaces the Jinja in code positions with placeholders, migrates the file as Python, then puts
the Jinja back byte for byte. Jinja inside string literals stays in place and is migrated as an Airflow
template.

## Results

### orchestration-reference

The [orchestration-reference](https://github.com/jeremydegardeyn/orchestration-reference) Cloud Composer DAGs,
rendered and parsed with Airflow 3.3.1:

| | DAGs that load | Import errors |
|---|:---:|:---:|
| Original | 0 / 8 | 8 |
| After `ruff --select AIR3 --fix --unsafe-fixes` | 0 / 8 | 8 (`days_ago`), then 4 (`provide_context`) once that is fixed by hand |
| **After dagshift** | **8 / 8** | **0** |

### Airflow's own example DAGs

The 62 example DAG files shipped with Airflow 2.11 (`airflow/example_dags`), upgraded to and loaded on each
Airflow 3 release:

| Airflow | Original: loaded / errors / deprecation warnings | After dagshift |
|---|---|---|
| 3.0.6 | 63 / 9 / 65 | 66 / 6 / 0 |
| 3.1.8 | 63 / 9 / 163 | 66 / 6 / 0 |
| 3.2.2 | 40 / 14 / 152 | 66 / 6 / 0 |
| 3.3.1 | 40 / 14 / 152 | 66 / 6 / 0 |

The 6 remaining errors are all reported as items that need you: a SubDAG example and a removed helper.

### Upgrades within Airflow 2

A 2.7-era project with 9 DAGs, loaded with real Airflow:

| Upgrade | DAGs | Import errors | Deprecation warnings before / after |
|---|---|---|---|
| 2.7 → 2.10, on 2.10.5 | 9 / 9 | 1 / 1 | 13 / 2 |
| 2.8 → 2.11, on 2.11.0 | 9 / 9 | 1 / 1 | 13 / 2 |

The remaining error and both warnings come from one file that imports `airflow.contrib`, which has no
replacement and is reported as an item that needs you.

## Compared with other approaches

| | Ruff `AIR3xx` | LLM agent skills | dagshift |
|---|---|---|---|
| Import path rewrites | yes (many as unsafe fixes) | via Ruff | yes, per target version |
| `days_ago`, `provide_context`, `schedule_interval`, removed context variables | flags some, fixes few | asks the model | fixed |
| Jinja-templated DAG files (`{% for %}` in code) | cannot parse | best effort | parsed and fixed |
| Airflow templates inside strings and YAML | no | best effort | fixed |
| Metadata DB access (sessions, ORM, `get_task_instances()`) | no | flags | flagged with a Task SDK recipe |
| Behaviour changes (cron timetables, catchup, default schedule) | no | flags | fixed or flagged, configurable |
| Deterministic, with SARIF export | yes | no | yes |
| Proves the result loads on the target | no | no | yes |

## Under the hood

The extension runs a local Python engine that it installs once into VS Code's storage. Files go through a
pipeline based on their kind:

* **Python** (DAGs, plugins, generators, templates): a chain of [libcst](https://libcst.readthedocs.io/)
  passes that preserve formatting and comments: strings, task context, metadata DB access, safety and
  behaviour checks, calls and arguments, replaced operators, then imports last so earlier passes still see the
  Airflow 2 names.
* **YAML, JSON and text**: format-preserving edits of operator paths, templates and keys.
* **Platform files** (requirements, Dockerfiles, Helm, Terraform, `airflow.cfg`): report only.

Every finding carries a rule id; the [rules reference](rules.md) lists them all with examples generated by
dagshift itself.
