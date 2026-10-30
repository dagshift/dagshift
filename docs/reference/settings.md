# Settings

Open them with **File > Preferences > Settings** and search for `dagshift`, or edit `settings.json`.

Most settings can also live in the repository, in `dagshift.toml` or `[tool.dagshift]` in `pyproject.toml`,
so the whole team gets the same upgrade. See [Current and target version](../guide/versions.md#share-the-versions-with-your-team).

## Versions

| Setting | Default | Description |
|---|---|---|
| `dagshift.target` | `3.3` | Airflow version to upgrade to, 2.7 to 3.3. When not set, `target` from the project's `dagshift.toml` or `[tool.dagshift]` is used. |
| `dagshift.sourceVersion` | `auto` | Version the project runs today. `auto` detects it; pick a version when the environment is defined somewhere dagshift cannot see. |
| `dagshift.sourcePaths` | `[]` | Extra files or folders to read the current version from, such as the Terraform repository for Composer or MWAA. Absolute, or relative to the workspace folder. |

## Upgrading

| Setting | Default | Description |
|---|---|---|
| `dagshift.upgrade.output` | `ask` | What *Upgrade Workspace* and *Upgrade Folder* do: `ask`, `inPlace` (refactor preview), or `folder` (write a copy). |
| `dagshift.upgrade.outputFolder` | `${folderName}-airflow-${target}` | Where a copy goes. Relative paths sit next to the upgraded folder. Variables: `${folderName}`, `${target}`, `${source}`. The folder must be empty or hold an earlier dagshift copy. |
| `dagshift.startDate` | *(today)* | Date used to turn `days_ago(n)` into a fixed `start_date`, as `YYYY-MM-DD`. |
| `dagshift.modernize` | `true` | Also update code that still works on the target but is deprecated there or has a newer home. |
| `dagshift.preserveScheduleDefault` | `true` | When crossing into Airflow 3, add `schedule=timedelta(days=1)` to DAGs without a schedule ([DS208](../rules.md#ds208)). |
| `dagshift.pinCatchup` | `false` | Add `catchup=True` where catchup was implicit ([DS209](../rules.md#ds209)). |
| `dagshift.keepCronDataIntervals` | `false` | Wrap cron and timedelta schedules in `CronDataIntervalTimetable` / `DeltaDataIntervalTimetable` so `ds`, `logical_date` and `data_interval_*` keep their Airflow 2 meaning ([DS503](../rules.md#ds503)). |
| `dagshift.renameConfigKeys` | `false` | Rename Airflow 2 parameter keys in YAML/JSON configs that are not dag-factory files ([DS704](../rules.md#ds704)). |
| `dagshift.migrateStrings` | `true` | Migrate Airflow code embedded in string literals (code generators). |

## Rules and scope

| Setting | Default | Description |
|---|---|---|
| `dagshift.select` | `[]` | Only run these rule ids or prefixes, for example `["DS1", "DS201"]`. Empty runs everything. |
| `dagshift.ignore` | `[]` | Rule ids or prefixes to skip. |
| `dagshift.exclude` | `[]` | Path globs, relative to the workspace folder, that dagshift does not scan. |

## Editor

| Setting | Default | Description |
|---|---|---|
| `dagshift.enable` | `true` | Enable dagshift in this workspace. |
| `dagshift.scanOnOpen` | `true` | Scan automatically when a folder with Airflow code opens. Files are never changed without confirmation. |
| `dagshift.lintOnType` | `true` | Re-check open files as you type. |
| `dagshift.lintDelay` | `700` | Milliseconds to wait after typing stops before re-checking. |
| `dagshift.showSummaryNotification` | `true` | Show a notification with the findings after the first scan. |
| `dagshift.showInfo` | `true` | Show informational findings in the Problems panel. |
| `dagshift.codeLens` | `true` | Show Preview / Apply at the top of files dagshift can migrate. |

## Validation

| Setting | Default | Description |
|---|---|---|
| `dagshift.validate.airflowPython` | | Python interpreter of an environment with the target Airflow installed. |
| `dagshift.validate.dagFolder` | | DAG folder to validate, relative to the workspace folder (for example `dags`). |
| `dagshift.validate.renderCommand` | | Shell command that renders templated DAGs first. It runs in a temporary copy, never in your workspace. |
| `dagshift.validate.pythonPath` | `[]` | Extra import roots for DAG files, relative to the workspace folder. |

## Engine

| Setting | Default | Description |
|---|---|---|
| `dagshift.interpreter` | | A Python that already has the dagshift engine installed. Empty lets the extension manage its own environment (recommended). |
| `dagshift.bootstrapPython` | | Python 3.10+ used to create the managed environment. Empty means the Python extension's interpreter, then `python3` / `python` on `PATH`. |

!!! note "Restricted Mode"

    In an untrusted workspace, dagshift ignores workspace-level values of `dagshift.interpreter`,
    `dagshift.bootstrapPython`, `dagshift.upgrade.outputFolder`, `dagshift.validate.airflowPython`,
    `dagshift.validate.renderCommand` and `dagshift.validate.pythonPath`, and validation is disabled.
