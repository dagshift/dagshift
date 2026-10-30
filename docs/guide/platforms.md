# Composer, MWAA and dag-factory

## Cloud Composer

Composer 3 has Airflow 3 images, but Composer does not yet upgrade an existing environment from Airflow 2 to
Airflow 3 in place. The usual path:

1. Create a second Composer environment on an Airflow 3 image.
2. In VS Code, run **dagshift: Upgrade Workspace...** and choose **Write an upgraded copy**. Your Airflow 2
   DAGs keep running unchanged.
3. Validate the copy on Airflow 3 and deploy it to the new environment.
4. Cut over, then retire the old environment.

Upgrades within Airflow 2 (or within Airflow 3) can be done in place.

dagshift reads `image_version` (for example `composer-2.9.7-airflow-2.10.5`) to detect the current version,
and reports it in Terraform files, but never edits it. Changing the environment is your call.

## Amazon MWAA

The current version is read from `airflow_version` in the MWAA environment definition (for example an
`aws_mwaa_environment` resource in Terraform). Like Composer image versions, it is reported and never edited.

## Astronomer

Astro Runtime images in Dockerfiles are recognized when detecting the current version.

## Infrastructure in a separate repository

Point dagshift at the repository that defines the environment, as described in
[Current and target version](versions.md#infrastructure-in-another-repository).

## dag-factory and homegrown configs

dag-factory 1.0 supports Airflow 3 and ships `dagfactory convert` and `dagfactory lint`. dagshift updates
import paths, template variables and renamed keys in dag-factory YAML, and points you to those commands for
layout changes.

For homegrown YAML or JSON pipelines, dagshift updates operator paths and Airflow templates. Renaming Airflow 2
parameter keys such as `schedule_interval` in those files is off by default, because only you know whether your
loader passes them to Airflow; turn it on with `dagshift.renameConfigKeys`.

## Limitations

* Values only known at render or run time (Jinja-injected schedules, `DAG(**config)`) cannot be checked
  statically. dagshift says where they are, and [validation](validate.md) with a render command checks the
  rendered output.
* Provider-level API changes beyond the replaced operators in [DS211](../rules.md#ds211) are surfaced by
  validation (import errors and deprecation warnings), not rewritten.
