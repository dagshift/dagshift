## Current version → target version

**Current version** is detected from, in order of trust:

- `requirements*.txt`, `pyproject.toml`, constraints URLs (`constraints-2.10.5/...`)
- Dockerfiles, docker-compose and Helm values (`apache/airflow:2.10.5`)
- Cloud Composer `image_version` (`composer-3-airflow-2.10.5-build.15`) and MWAA `airflow_version`
- Astro Runtime images
- the code itself, when nothing is pinned (for example `airflow.sdk` imports mean 3.x)

If your Composer or MWAA environment is defined in a **separate Terraform repository**, choose
*Detect from another folder or file...* and point at it, or pick the version from the list. The choice is saved
in the workspace settings (`dagshift.sourceVersion`, `dagshift.sourcePaths`). For the CLI and CI, put it in
`pyproject.toml`:

```toml
[tool.dagshift]
source = "2.10"
target = "3.3"
```

**Target version** can be any release from 2.7 to 3.3. Each one has its own rule set, measured on a real install:
an upgrade from 2.8 to 2.11 only applies 2.x deprecation fixes, 2.10 to 3.3 adds everything Airflow 3 changed, and
3.0 to 3.3 picks up what changed inside 3.x (for example `airflow.datasets` disappearing in 3.2).
