# Changelog

## 0.1.1

- Windows: the *Preview Upgrade Changes* button now shows in the editor title bar and the Explorer context
  menu. It never appeared on Windows before.
- Engine setup finds Python 3.14 and the plain `py -3` launcher on Windows, and `python3.14` on macOS and
  Linux, when no older supported Python is installed.
- More robust engine process handling: a crashed engine no longer raises an unhandled pipe error, and an
  engine that fails to start is always stopped.
- Validation no longer fails on non-UTF-8 output from a render command or the Airflow interpreter.
- Rule codes in the Problems panel link to the new documentation site, https://dagshift.github.io/dagshift/.
- Error messages no longer suggest installing the engine from PyPI, where it is not published.

## 0.1.0

- First release.
- Detects the Airflow version a project runs today from requirements, constraints, Dockerfiles, compose, Helm,
  Cloud Composer, MWAA and Astro Runtime pins, or from the code when nothing is pinned.
- Current and target versions can both be changed from the status bar. The current version can also be
  detected from another folder or file, such as a Terraform repository that defines the Composer or MWAA
  environment.
- Upgrades from any supported version to a newer one: 2.x to 2.y, 2.x to 3.y, 3.x to 3.y (targets 2.7 to 3.3).
- Scans folders with Airflow code on open, shows findings as diagnostics with fix recipes, previews per-file
  diffs, and validates DAG folders on a real Airflow install.
- Upgrading a workspace or folder either edits the files in place (through VS Code's refactor preview) or
  writes an upgraded copy to a separate folder and leaves the originals untouched, with a multi-file compare
  view and an upgrade report in the copy.
- Engine: dagshift 0.1.0, rules verified against Airflow 2.7.3, 2.8.4, 2.9.3, 2.10.5, 2.11.0, 3.0.6, 3.1.8,
  3.2.2 and 3.3.1.
