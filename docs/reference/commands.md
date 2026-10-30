# Commands

Run these from the Command Palette (++ctrl+shift+p++, or ++cmd+shift+p++ on macOS). They all start with
**dagshift:**. Most are also in the **Airflow Upgrade** view's title bar, the Explorer context menu, or the
CodeLens at the top of a file.

## Versions

| Command | What it does |
|---|---|
| **Select Current and Target Airflow Versions...** | Same as clicking the status bar. Change either end of the upgrade. |
| **Select Current Airflow Version...** | Detect automatically, detect from another folder or file, or pick a version. |
| **Select Target Airflow Version...** | Pick the version to upgrade to, or fall back to the project's `dagshift.toml`. |

## Review

| Command | What it does |
|---|---|
| **Scan Workspace for Airflow Upgrade Issues** | Scan again (also runs on open, unless `dagshift.scanOnOpen` is off). |
| **Show Upgrade Report** | Open a Markdown report of everything found, grouped by file. |
| **Export Report...** | Save the report as Markdown, JSON, SARIF or plain text. |
| **Show How to Fix** | Open the recipe for a finding. In the Airflow Upgrade view, next to items that need you. |
| **Ignore Rule in This Workspace** | Add a finding's rule to `dagshift.ignore`. In the Airflow Upgrade view's context menu. |

## Upgrade

| Command | What it does |
|---|---|
| **Preview Upgrade Changes** | Open a diff of the current file against its upgraded version. |
| **Apply Upgrade Fixes to File** | Apply the safe fixes to the current file. |
| **Apply These Changes** | From an open preview diff, write it into the file. |
| **Upgrade Workspace...** | Upgrade every file, in place or as a copy. |
| **Upgrade Folder...** | The same, for one folder. In the Explorer context menu. |
| **Write Upgraded Copy to a Folder...** | Skip the question and write a copy. |
| **Compare Upgraded Copy with Originals** | Open every changed file of the last copy in one multi-file diff. |

## Validate

| Command | What it does |
|---|---|
| **Validate DAGs on Target Airflow...** | Load the DAG folder with the target Airflow's DagBag and report import errors and deprecation warnings. |

## Engine

| Command | What it does |
|---|---|
| **Restart Engine** | Restart the background engine and scan again. |
| **Reinstall Engine** | Recreate the managed engine environment, for example after changing Python. |
| **Show Log** | Open the dagshift output channel, useful when reporting an issue. |
