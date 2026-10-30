# dagshift

Deterministic Airflow upgrades for DAG code and everything around it: from the version you run today to any
newer one (2.7 through 3.3), Airflow 2 to 2, 2 to 3, or 3 to 3.

dagshift detects the version a project runs, rewrites what can be rewritten without changing behaviour, and
reports everything else with a concrete fix. It works on any shape of Airflow project: one-off DAG files,
TaskFlow, dynamic generators, custom operators and plugins, Jinja-templated DAG factories, YAML/JSON-driven
pipelines (dag-factory or homegrown), code generators that emit DAGs as strings, plus the requirements, images
and infrastructure files that pin Airflow.

```
pip install dagshift
dagshift detect  .                                  # which Airflow does this project run today?
dagshift check   dags/ plugins/ configs/ --target 3.3        # what changes, what needs you
dagshift migrate dags/ plugins/ configs/ --target 3.3 --out upgraded/ --report UPGRADE.md
dagshift validate upgraded/dags --airflow-python /venvs/airflow-3.3/bin/python
```

There is also a VS Code extension, [dagshift: Airflow Upgrade Assistant](https://github.com/dagshift/dagshift/tree/main/editors/vscode): open a
folder of DAGs and it detects the current version, lets you pick the target from a list, and either edits the
files in place after a diff preview or writes an upgraded copy to a separate folder.

`migrate --out DIR` writes a full copy (unchanged files included) and marks it with `.dagshift-upgrade.json`, so
later scans of the project skip it even when the copy sits inside the project.

## Current and target version

Every run has a current version (where the code is today) and a target (where it is going). The rules that fire
depend on both:

| upgrade | what changes |
|---|---|
| 2.x to 2.y | deprecated import paths, `schedule_interval` and `concurrency`, `days_ago`, deprecated context variables, operator arguments |
| 2.x to 3.y | all of the above plus moved operators, `airflow.sdk`, Datasets to Assets, metadata DB access, cron timetable semantics, schedule and catchup defaults, plugins, SLAs |
| 3.x to 3.y | what changed inside Airflow 3 (for example `airflow.datasets` removed in 3.2, exceptions moving to `airflow.sdk.exceptions`) |

**Target**: `--target 3.1` (or `2.11`, `latest`, `3.3.1`). Default: the newest supported version.

**Current version**: detected unless you pass `--source 2.10.5`. dagshift reads, in order of trust:

* Cloud Composer `image_version` (`composer-2.9.7-airflow-2.10.5`), Amazon MWAA `airflow_version`, Astro Runtime images
* `FROM apache/airflow:...` in Dockerfiles, compose files, Helm `airflowVersion` / `defaultAirflowTag`
* constraints URLs, `apache-airflow==` / `~=` / `>=x,<y` in requirements, pyproject and setup files
* when nothing is pinned, the code itself (`airflow.sdk` means 3.0+, `DatasetAlias` 2.10+, `@task.bash` 2.9+,
  `.expand()` 2.3+ and so on), reported as "inferred"

When the infrastructure lives in another repository (Terraform for Composer or MWAA next to, but not inside,
the DAG repo), point at it:

```
$ dagshift detect dags/
Airflow 2.4 (inferred)
No pinned version found; the code needs at least Airflow 2.4 (uses Datasets). Set the current version if your environment is newer.
  2.4      code         taskflow_assets.py:3  uses Datasets
  2.3      code         taskflow_assets.py:7  uses EmptyOperator
  2.0      code         guarded_and_cron.py:5  uses TaskFlow decorators

$ dagshift detect dags/ --source-path ../platform-terraform
Airflow 2.9.3 (pinned)
  2.9.3    composer     platform-terraform/composer/main.tf:6  composer-3-airflow-2.9.3

$ dagshift check dags/ --source-path ../platform-terraform --target 3.3
```

Or write both versions down once, so CI and the editor agree:

```toml
[tool.dagshift]
source = "2.9"
target = "3.3"
```

dagshift only upgrades: a target older than the current version is an error.

## Why another tool

| | Ruff `AIR3xx` | LLM agent skills | dagshift |
|---|---|---|---|
| Import path rewrites | yes (many as unsafe fixes) | via ruff | yes, per target version |
| `days_ago`, `provide_context`, `schedule_interval`, removed context variables | flags some, fixes few | asks the model | fixed |
| Jinja-templated DAG files (`{% for %}` in code) | cannot parse | best effort | parsed and fixed |
| Airflow templates inside strings and YAML | no | best effort | fixed |
| Metadata DB access (sessions, ORM, `get_task_instances()`) | no | flags | flagged with Task SDK recipe |
| Behaviour changes (cron timetables, catchup, default schedule) | no | flags | fixed or flagged, configurable |
| Deterministic, CI friendly (exit codes, SARIF) | yes | no | yes |
| Proves the result loads on Airflow 3 | no | no | `dagshift validate` |

On the [orchestration-reference](https://github.com/jeremydegardeyn/orchestration-reference)
Composer DAGs, rendered and parsed with Airflow 3.3.1:

| | DAGs that load | import errors |
|---|---|---|
| original | 0 / 8 | 8 |
| after `ruff --select AIR3 --fix --unsafe-fixes` | 0 / 8 | 8 (`days_ago`), then 4 (`provide_context`) once that is fixed by hand |
| after `dagshift migrate` | 8 / 8 | 0 |

On a 2.7-era fixture project (`tests/fixtures/project/dags`) upgraded within Airflow 2, loaded with real
Airflow:

| upgrade | DAGs | import errors | deprecation warnings before / after |
|---|---|---|---|
| 2.7 to 2.10, on 2.10.5 | 9 / 9 | 1 / 1 | 13 / 2 |
| 2.8 to 2.11, on 2.11.0 | 9 / 9 | 1 / 1 | 13 / 2 |

The remaining error and both warnings come from one file that imports `airflow.contrib`, which has no
replacement and is reported as a manual item.

On Airflow 2.11's own 62 example DAG files (`airflow/example_dags`):

| Airflow | original: loaded / errors / deprecation warnings | after dagshift |
|---|---|---|
| 3.0.6 | 63 / 9 / 65 | 66 / 6 / 0 |
| 3.1.8 | 63 / 9 / 163 | 66 / 6 / 0 |
| 3.2.2 | 40 / 14 / 152 | 66 / 6 / 0 |
| 3.3.1 | 40 / 14 / 152 | 66 / 6 / 0 |

The 6 remaining errors are all reported as manual items (a SubDAG example and a removed helper).

## How it stays correct

**The knowledge base is measured, not typed.** `tools/build_kb.py` installs every supported Airflow release
(2.7.3, 2.8.4, 2.9.3, 2.10.5, 2.11.0, 3.0.6, 3.1.8, 3.2.2, 3.3.1), imports every legacy path DAG code uses
(plus Airflow 2's own deprecated-path tables), and records for each version whether the old path is ok,
deprecated or removed and which replacement is importable and is the same object. It also records the DAG,
operator and `Variable.get` parameters, context keys and plugin attributes each version accepts. Provider
targets are checked against the current provider wheels. The result ships as `dagshift/_data/kb.json`
(860 paths, 9 targets).

**Semantics first.** A rewrite happens only when the new code means the same thing:

* `days_ago(2)` becomes the fixed date it returned on the day you migrate (configurable).
* `{{ next_ds }}` becomes `{{ data_interval_end | ds }}`; `{{ prev_ds }}` has no equivalent, so it is reported.
* A DAG without `schedule` gets `schedule=timedelta(days=1)` when the upgrade crosses into Airflow 3, because Airflow 2 ran it daily and Airflow 3 would never run it.
* Cron schedules keep their string by default and are flagged when the file reads `ds`/`logical_date`,
  because Airflow 3 runs them with `CronTriggerTimetable`. `--keep-cron-data-intervals` wraps them in
  `CronDataIntervalTimetable` instead.

**It proves the output.** `dagshift validate` loads DAGs with the target Airflow interpreter (optionally after
running your render command) and reports import errors and deprecation warnings. The test suite does this
against real Airflow 2 and 3 installs.

**Idempotent.** Running `migrate` on its own output changes nothing.

## Architecture

```
paths ──► engine.classify ──► python  ─► jinja_mask ─► libcst passes ─► unmask ─► FileResult
                         ├──► yaml/json/text ─► text_migrator (format preserving)  ─► FileResult
                         └──► platform files ─► platform_rules (report only)       ─► Findings
                                                                                      │
          report.py (text / markdown / json / sarif) ◄── MigrationRun ◄──────────────┘
          validate.py (DagBag in the target interpreter)
```

Python passes, in order (each is a libcst codemod sharing one `FileState`):

| pass | rules |
|---|---|
| `StringsPass` | templates in strings, Airflow code embedded in strings (recursive) |
| `ContextPass` | context keys, task-callable parameters, `.execution_date`, `xcom_pull` |
| `MetadataDbPass` | sessions, ORM models, DB-backed runtime methods |
| `SafetyPass` | guarded imports, parse-time values, schedule semantics, plugins |
| `CallsPass` | `days_ago`, `apply_defaults`, DAG/operator arguments, defaults, `Variable.get` |
| `ReplacedOperatorsPass` | operators removed from providers (`PostgresOperator` & co) |
| `ImportsPass` | moved, renamed and removed imports (last, so earlier passes see Airflow 2 names) |

Templated files: Jinja in code positions is replaced with identifiers (or comments for statement lines),
the file is migrated as Python, and the Jinja is put back byte for byte. Jinja inside string literals is
left in place and migrated as Airflow templates.

## Rules

Run `dagshift rules` for the full list, or read [docs/rules.md](https://github.com/dagshift/dagshift/blob/main/docs/rules.md) for every rule with an example
generated by dagshift itself. Severity tells you what happened:

* **fixed**: rewritten, behaviour preserved
* **manual**: breaks on the target and has no safe rewrite; a recipe is attached
* **warn**: still imports, but behaves differently
* **info**: worth knowing before cutover

| range | area |
|---|---|
| DS1xx | import paths (moved, modernized to `airflow.sdk`, removed, `airflow.contrib`/`www`/SubDAG) |
| DS2xx | removed functions and changed signatures (`days_ago`, DAG/operator arguments, defaults, SLAs, replaced operators) |
| DS3xx | task context and templates (`execution_date`, `next_ds`, `conf`, `xcom_pull` without `task_ids`) |
| DS4xx | metadata DB access from task code |
| DS5xx | behaviour changes (guarded imports, parse-time values, schedule semantics, plugins, templated files) |
| DS6xx | code generators (Airflow code inside string literals) |
| DS7xx | YAML/JSON configs (dag-factory and homegrown) |
| DS8xx | environment (version pins, providers, `.airflowignore`, Cloud Composer, MWAA, config) |

## CLI

```
dagshift detect PATH... [--source-path PATH] [--format text|json]
dagshift check PATH... [--target 3.3] [--source 2.10] [--source-path PATH]
                       [--format text|md|json|sarif] [--fail-on any|manual|warn|never]
dagshift migrate PATH... (--out DIR | --in-place) [--target ...] [--source ...] [--source-path ...]
                         [--diff FILE] [--report FILE.md] [--json FILE] [--sarif FILE]
dagshift validate DAG_FOLDER --airflow-python PY [--render-cmd "..."] [--pythonpath DIR]
dagshift rules
dagshift targets
dagshift serve                       # JSON over stdio, used by the VS Code extension
```

Common switches: `--start-date YYYY-MM-DD`, `--no-modernize`, `--no-preserve-schedule`, `--pin-catchup`,
`--keep-cron-data-intervals`, `--rename-config-keys`, `--no-strings`, `--select DS1,DS2`, `--ignore DS102`,
`--exclude "tests/*"`.

DAG factories that render at build time:

```
dagshift validate dags --airflow-python /venvs/airflow-3.3/bin/python \
  --render-cmd "PYTHONPATH=. python framework/dag_builder.py" --pythonpath .
```

The render command runs in a temporary copy, so your tree is never touched.

## Configuration

`[tool.dagshift]` in `pyproject.toml`, or a `dagshift.toml`, is picked up automatically:

```toml
[tool.dagshift]
source = "2.10"                    # current version; omit to detect it
source_paths = ["../platform-terraform"]   # where to detect it from, besides the paths you pass
target = "3.3"
start_date = "2026-01-01"          # date used to pin days_ago()
keep_cron_data_intervals = true    # keep Airflow 2 logical_date/ds semantics for cron schedules
pin_catchup = false
exclude = ["tests/*", "scratch/*"]
ignore = ["DS102"]                 # do not modernize imports that still work

[tool.dagshift.extra_moves]        # your own libraries
"acme.airflow.ops.LegacyLoadOperator" = "acme.airflow.v3.LoadOperator"
```

## Python API

```python
from dagshift import Options, migrate_source, migrate_paths, validate

from dagshift import detect_version

det = detect_version(["dags"], extra=["../platform-terraform"])
det.version, det.confidence, det.evidence     # "2.9.3", "pinned", [Evidence(...)]

res = migrate_source(code, filename="dags/etl.py", options=Options(source="2.9", target="3.1"))
res.migrated, res.findings

run = migrate_paths(["dags", "configs"], Options(target="3.3"))   # current version detected
run.write_to("upgraded")               # or run.write_in_place()
print(run.diff())

check = validate("upgraded/dags", "/venvs/airflow-3.3/bin/python")
assert check.ok, check.to_text()
```

## Extending

Rule packs register through the `dagshift.rules` entry point group:

```toml
[project.entry-points."dagshift.rules"]
acme = "acme_dagshift:register"
```

```python
from dagshift.python.base import Pass

class AcmeOperatorRename(Pass):
    def leave_Call(self, original_node, updated_node):
        return updated_node

def register(registry):
    registry.add_python_pass(AcmeOperatorRename)
    registry.add_moves({"acme.ops.Old": "acme.ops.New"})
```

## CI

```yaml
- run: pip install dagshift
- run: dagshift check dags plugins --target 3.3 --format sarif --output dagshift.sarif --fail-on manual
- uses: github/codeql-action/upload-sarif@v3
  with: { sarif_file: dagshift.sarif }
```

## Platform notes

* **Cloud Composer**: Composer 3 has Airflow 3 images, but Composer does not yet support upgrading an existing
  environment from Airflow 2 to 3 in place. Create a second environment, migrate the DAGs with dagshift,
  validate, cut over. Upgrades within Airflow 2 (or within Airflow 3) can be done in place.
  dagshift reports `image_version` lines and never edits them.
* **dag-factory**: dag-factory 1.0 supports Airflow 3 and ships `dagfactory convert` and `dagfactory lint`.
  dagshift updates import paths, template variables and renamed keys, and points you to those commands for
  layout changes.

## Limitations

* Values only known at render or run time (Jinja-injected schedules, `DAG(**config)`) cannot be checked
  statically; dagshift says where they are and `validate --render-cmd` checks the rendered output.
* Provider-level API changes beyond the ones listed in DS211 are surfaced by `validate` (import errors and
  deprecation warnings), not rewritten.
* On Airflow 3.0, `validate` parses in-process, so top-level `airflow.sdk.Variable.get` calls fail there
  even though a real DAG processor handles them; the report says so.

## Refreshing the knowledge base

See `tools/README.md`. When a new Airflow release ships: install it, run `tools/build_kb.py`, run
`tools/gen_rules_doc.py`, run the tests.

## License

Apache-2.0
