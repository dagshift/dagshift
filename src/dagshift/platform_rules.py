"""Environment and platform files. These rules only report; changing a platform version is a
deployment decision (and on some platforms an in-place change is not supported).

DS801  Airflow pinned below the target (requirements, pyproject, setup.py, Dockerfile, compose, Helm values)
DS802  provider packages that migrated imports now need (standard, common.sql, smtp)
DS803  .airflowignore written as regular expressions (Airflow 3 defaults to glob syntax)
DS804  Cloud Composer image below the target
DS805  Amazon MWAA environment below the target
DS806  airflow.cfg / config overrides for the removed webserver
"""
from __future__ import annotations

import re
from pathlib import PurePath

from .findings import Finding, Severity
from .kb import KnowledgeBase

REQUIREMENTS_NAMES = re.compile(
    r"^(requirements.*\.(txt|in)|constraints.*\.txt|pyproject\.toml|setup\.py|setup\.cfg|Pipfile|environment\.ya?ml)$"
)
_PROVIDER = re.compile(r"apache-airflow-providers-([a-z0-9-]+)", re.I)
_DOCKER_FROM = re.compile(r"^\s*FROM\s+(\S*apache/airflow):(\S+)", re.I | re.M)
_ASTRO_FROM = re.compile(r"^\s*FROM\s+(\S*astro-runtime):(\S+)", re.I | re.M)
_WEBSERVER_OVERRIDE = re.compile(r'["\']?webserver-([\w]+)["\']?\s*[=:]')
_REGEXY = re.compile(r"(\.\*|\\\.|\^|\$|\(|\)|\||\+|\[\^)")

_TWO_SEGMENT_PROVIDERS = {"apache", "cncf", "common", "microsoft", "atlassian", "dbt", "fab"}


def provider_package(module: str) -> str | None:
    """airflow.providers.common.sql.operators.sql -> apache-airflow-providers-common-sql"""
    parts = module.split(".")
    if len(parts) < 3 or parts[:2] != ["airflow", "providers"]:
        return None
    pid = f"{parts[2]}.{parts[3]}" if parts[2] in _TWO_SEGMENT_PROVIDERS and len(parts) > 3 else parts[2]
    return "apache-airflow-providers-" + pid.replace(".", "-").replace("_", "-")


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def is_requirements(name: str) -> bool:
    return bool(REQUIREMENTS_NAMES.match(name))


def scan_platform_file(path: str, text: str, kb: KnowledgeBase) -> list[Finding]:
    """Version pins and environment settings that must change for the upgrade (reported, never edited)."""
    from .versions import vtuple

    name = PurePath(path).name
    out: list[Finding] = []
    tgt = kb.target
    crossing = kb.crosses("3.0")

    def add(rule, sev, offset, msg, recipe=None):
        out.append(Finding(rule, sev, path, _line(text, offset), msg, recipe))

    def behind(version: str) -> bool:
        return vtuple(version) < kb.target_t

    constraints = (f"Install with the constraints file of the exact {tgt}.x release: https://raw.githubusercontent.com/"
                   f"apache/airflow/constraints-{tgt}.<patch>/constraints-<python>.txt")
    if is_requirements(name):
        for mt in re.finditer(r"apache-airflow(\[[^\]]*\])?\s*(==|~=|<=|<)\s*v?(\d+(?:\.\d+){1,2})", text, re.I):
            if behind(mt.group(3)) or (mt.group(2) in ("<", "<=") and vtuple(mt.group(3)) <= kb.target_t):
                add("DS801", Severity.MANUAL, mt.start(),
                    f"Airflow is pinned to {mt.group(0).strip()}; the upgrade targets {tgt}.",
                    f"Pin apache-airflow to the {tgt}.x release you are moving to. {constraints}")
        for mt in re.finditer(r"(apache-airflow-providers-[a-z0-9-]+)\s*==\s*([\w.]+)", text, re.I):
            add("DS801", Severity.INFO, mt.start(),
                f"{mt.group(1)} is pinned to {mt.group(2)}.",
                f"Take provider versions from the constraints file of Airflow {tgt}.x; older provider releases may "
                "not support it.")
    if name.lower().startswith("dockerfile") or name.lower().endswith(".dockerfile"):
        for mt in _DOCKER_FROM.finditer(text):
            tag = mt.group(2)
            ver = tag.replace("slim-", "").split("-")[0]
            if behind(ver):
                add("DS801", Severity.MANUAL, mt.start(), f"Base image {mt.group(1)}:{tag} runs Airflow {ver}.",
                    f"Use an apache/airflow:{tgt}.x image (or slim-{tgt}.x).")
        for mt in _ASTRO_FROM.finditer(text):
            add("DS801", Severity.INFO, mt.start(), f"Astro Runtime image {mt.group(2)}.",
                f"Pick the Astro Runtime release that ships Airflow {tgt}.x.")
    if re.match(r"(docker-)?compose.*\.ya?ml$", name):
        for mt in re.finditer(r"apache/airflow:(?:slim-)?v?(\d+\.\d+(?:\.\d+)?)", text):
            if behind(mt.group(1)):
                add("DS801", Severity.MANUAL, mt.start(), f"Compose file uses apache/airflow:{mt.group(1)}.",
                    f"Switch to apache/airflow:{tgt}.x."
                    + (" Airflow 3 starts `api-server` instead of `webserver` and needs a separate `dag-processor` "
                       "service." if crossing else ""))
    if name.endswith((".yaml", ".yml")):
        for mt in re.finditer(r"^\s*(airflowVersion|defaultAirflowTag)\s*:\s*['\"]?(\d+\.\d+(?:\.\d+)?)", text, re.M):
            if behind(mt.group(2)):
                add("DS801", Severity.MANUAL, mt.start(), f"Helm values set {mt.group(1)}: {mt.group(2)}.",
                    f"Set it to {tgt}.x and use an Airflow Helm chart release that supports it.")
    if name.endswith((".tf", ".tfvars", ".hcl", ".json", ".yaml", ".yml")):
        for mt in re.finditer(r"composer-(\d+)(?:\.[\d.]+)?-airflow-(\d+\.\d+(?:\.\d+)?)", text):
            gen, ver = mt.group(1), mt.group(2)
            if not behind(ver):
                continue
            if crossing and vtuple(ver) < (3, 0):
                recipe = ("Composer does not yet support upgrading an environment from Airflow 2 to 3 in place: create "
                          f"a second Composer 3 environment on composer-3-airflow-{tgt}.x-build.N and cut over. Do not "
                          "just change image_version on the existing resource.")
            else:
                recipe = (f"Composer can upgrade within the same Airflow major in place: move image_version to a "
                          f"composer-{gen}-airflow-{tgt}.x build (check the Composer version list for the newest build).")
            add("DS804", Severity.INFO, mt.start(), f"Cloud Composer image {mt.group(0)} runs Airflow {ver}.", recipe)
        if "aws_mwaa_environment" in text or "AWS::MWAA::Environment" in text:
            for mt in re.finditer(r'(?:airflow_version\s*=\s*"|AirflowVersion\s*:\s*[\'"]?)(\d+\.\d+(?:\.\d+)?)', text):
                if behind(mt.group(1)):
                    add("DS805", Severity.INFO, mt.start(), f"Amazon MWAA environment on Airflow {mt.group(1)}.",
                        f"Check that MWAA offers Airflow {tgt} and its documented upgrade path before cutover.")
        if crossing:
            for mt in _WEBSERVER_OVERRIDE.finditer(text):
                add("DS806", Severity.INFO, mt.start(),
                    f"Config override for [webserver] {mt.group(1)}: Airflow 3 replaces the webserver with the "
                    "API server, and several [webserver] options moved or were removed.",
                    "Run `airflow config update` against the Airflow 3 config to see the renamed options.")
    if name == "airflow.cfg":
        add("DS806", Severity.INFO, 0, "airflow.cfg found.",
            f"Run `airflow config update --fix` with Airflow {tgt} installed to rename and drop changed options.")
    if name == ".airflowignore" and crossing:
        for i, line in enumerate(text.splitlines(), start=1):
            st = line.strip()
            if st and not st.startswith("#") and _REGEXY.search(st):
                out.append(Finding("DS803", Severity.WARN, path, i,
                                   f".airflowignore line `{st}` looks like a regular expression; Airflow 3 reads "
                                   "this file as glob patterns by default.",
                                   "Rewrite it as a glob (e.g. `.*_test\\.py` -> `*_test.py`), or set "
                                   "[core] dag_ignore_file_syntax = regexp."))
    return out


def providers_listed(text: str) -> set[str]:
    return {"apache-airflow-providers-" + m.group(1).lower() for m in _PROVIDER.finditer(text)}
