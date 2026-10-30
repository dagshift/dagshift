---
title: Upgrade Airflow DAGs from VS Code
hide:
  - navigation
  - toc
---

<div class="ds-hero" markdown>

![dagshift](assets/icon.png){ .ds-logo }

# dagshift

**Upgrade Apache Airflow DAGs to any newer version, right inside VS Code.**
{ .ds-tagline }

2.x → 2.y &nbsp;·&nbsp; 2 → 3 &nbsp;·&nbsp; 3.x → 3.y &nbsp;·&nbsp; a diff before anything is written
{ .ds-sub }

[Install from the Marketplace](https://marketplace.visualstudio.com/items?itemName=dagshift.dagshift){ .md-button .md-button--primary }
[Get started](getting-started.md){ .md-button }

</div>

![dagshift in VS Code: findings, status bar and a diff](assets/screenshots/overview.png){ .ds-shot }

## What it does

<div class="grid cards" markdown>

-   :material-radar:{ .lg .middle } **Finds the version you run today**

    ---

    Reads requirements, constraints URLs, Dockerfiles, compose, Helm, Cloud Composer `image_version`,
    MWAA and Astro Runtime. Infrastructure in a separate Terraform repository? Point at it.

    [:octicons-arrow-right-24: Current and target version](guide/versions.md)

-   :material-target:{ .lg .middle } **Upgrades to the version you pick**

    ---

    Any target from 2.7 to 3.3. Each target has its own rule set, measured on a real install of that
    Airflow release.

    [:octicons-arrow-right-24: Rules](rules.md)

-   :material-auto-fix:{ .lg .middle } **Fixes what is safe to fix**

    ---

    Import paths, `days_ago`, `schedule_interval`, removed context variables, Datasets to Assets,
    operator arguments, templates inside strings and YAML.

    [:octicons-arrow-right-24: Preview and upgrade](guide/upgrade.md)

-   :material-lightbulb-on-outline:{ .lg .middle } **Explains the rest**

    ---

    Metadata database access, SubDAGs, Flask plugins and cron semantics come with a concrete recipe
    instead of a guess.

    [:octicons-arrow-right-24: Review findings](guide/review.md)

-   :material-file-compare:{ .lg .middle } **Diff first, always**

    ---

    Edit in place through VS Code's refactor preview, or write an upgraded copy next to the project and
    leave the originals untouched.

    [:octicons-arrow-right-24: Preview and upgrade](guide/upgrade.md)

-   :material-check-decagram:{ .lg .middle } **Proves the result loads**

    ---

    Loads your DAG folder with Airflow's own DagBag in an environment you choose, and reports import
    errors and deprecation warnings.

    [:octicons-arrow-right-24: Validate on real Airflow](guide/validate.md)

</div>

## On real DAGs

The eight Cloud Composer DAGs in
[orchestration-reference](https://github.com/jeremydegardeyn/orchestration-reference), rendered and parsed
with Airflow 3.3.1:

| | DAGs that load | Import errors |
|---|:---:|:---:|
| Original | 0 / 8 | 8 |
| After `ruff --select AIR3 --fix --unsafe-fixes` | 0 / 8 | 8, then 4 once `days_ago` is fixed by hand |
| **After dagshift** | **8 / 8** | **0** |

More numbers, including Airflow's own example DAGs, are in [How it works](how-it-works.md#results).

## Works with every shape of Airflow project

Plain DAG files, TaskFlow, dynamic DAG generators, custom operators and plugins, Jinja-templated DAG
factories, dag-factory and homegrown YAML/JSON pipelines, code generators that emit DAGs as strings, and the
requirements, images and infrastructure files that pin Airflow.

!!! tip "Everything runs on your machine"

    The extension sends nothing anywhere. The only network access is a one-time `pip install` of the
    engine's single dependency (`libcst`) from your configured package index.
