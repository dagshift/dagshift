# Changelog

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
