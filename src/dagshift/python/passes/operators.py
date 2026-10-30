"""Operators that were removed from their provider and replaced by a different class.

DS211  PostgresOperator, MySqlOperator, SqliteOperator, MsSqlOperator, OracleOperator, JdbcOperator,
       VerticaOperator, SnowflakeOperator -> SQLExecuteQueryOperator (common.sql), with
       <db>_conn_id -> conn_id and connection-level settings -> hook_params;
       SimpleHttpOperator -> HttpOperator.

Upgrading to Airflow 3 means upgrading providers, and current provider releases no longer
ship these classes, so the rewrite is part of the migration.
"""
from __future__ import annotations

import libcst as cst
from libcst.metadata import PositionProvider, QualifiedNameProvider

from ...findings import Severity
from ..base import Pass
from ..helpers import kwarg, qnames

SQL_EXEC = ("airflow.providers.common.sql.operators.sql", "SQLExecuteQueryOperator")
HTTP = ("airflow.providers.http.operators.http", "HttpOperator")


def _sql(conn_kw: str, hook_kws: tuple[str, ...] = ()) -> dict:
    return {"new": SQL_EXEC, "renames": {conn_kw: "conn_id"}, "hook_params": hook_kws}


_DB = {
    "postgres": _sql("postgres_conn_id"),
    "mysql": _sql("mysql_conn_id"),
    "sqlite": _sql("sqlite_conn_id"),
    "mssql": _sql("mssql_conn_id"),
    "oracle": _sql("oracle_conn_id"),
    "jdbc": _sql("jdbc_conn_id"),
    "vertica": _sql("vertica_conn_id"),
    "snowflake": _sql("snowflake_conn_id",
                      ("warehouse", "role", "schema", "authenticator", "session_parameters")),
}
REPLACEMENTS: dict[str, dict] = {
    "airflow.providers.postgres.operators.postgres.PostgresOperator": _DB["postgres"],
    "airflow.operators.postgres_operator.PostgresOperator": _DB["postgres"],
    "airflow.providers.mysql.operators.mysql.MySqlOperator": _DB["mysql"],
    "airflow.operators.mysql_operator.MySqlOperator": _DB["mysql"],
    "airflow.providers.sqlite.operators.sqlite.SqliteOperator": _DB["sqlite"],
    "airflow.operators.sqlite_operator.SqliteOperator": _DB["sqlite"],
    "airflow.providers.microsoft.mssql.operators.mssql.MsSqlOperator": _DB["mssql"],
    "airflow.operators.mssql_operator.MsSqlOperator": _DB["mssql"],
    "airflow.providers.oracle.operators.oracle.OracleOperator": _DB["oracle"],
    "airflow.operators.oracle_operator.OracleOperator": _DB["oracle"],
    "airflow.providers.jdbc.operators.jdbc.JdbcOperator": _DB["jdbc"],
    "airflow.operators.jdbc_operator.JdbcOperator": _DB["jdbc"],
    "airflow.providers.vertica.operators.vertica.VerticaOperator": _DB["vertica"],
    "airflow.contrib.operators.vertica_operator.VerticaOperator": _DB["vertica"],
    "airflow.providers.snowflake.operators.snowflake.SnowflakeOperator": _DB["snowflake"],
    "airflow.contrib.operators.snowflake_operator.SnowflakeOperator": _DB["snowflake"],
    "airflow.providers.standard.sensors.time_delta.TimeDeltaSensorAsync": {
        "new": ("airflow.providers.standard.sensors.time_delta", "TimeDeltaSensor"), "renames": {},
        "hook_params": (), "add": {"deferrable": "True"}},
    "airflow.sensors.time_delta.TimeDeltaSensorAsync": {
        "new": ("airflow.providers.standard.sensors.time_delta", "TimeDeltaSensor"), "renames": {},
        "hook_params": (), "add": {"deferrable": "True"}},
    "airflow.providers.standard.sensors.time.TimeSensorAsync": {
        "new": ("airflow.providers.standard.sensors.time", "TimeSensor"), "renames": {},
        "hook_params": (), "add": {"deferrable": "True"}},
    "airflow.sensors.time_sensor.TimeSensorAsync": {
        "new": ("airflow.providers.standard.sensors.time", "TimeSensor"), "renames": {},
        "hook_params": (), "add": {"deferrable": "True"}},
    "airflow.providers.http.operators.http.SimpleHttpOperator": {"new": HTTP, "renames": {}, "hook_params": ()},
    "airflow.operators.http_operator.SimpleHttpOperator": {"new": HTTP, "renames": {}, "hook_params": ()},
}


class ReplacedOperatorsPass(Pass):
    DESCRIPTION = "Operators replaced by a different class in current providers"
    REQUIRES_ANY = tuple({k.rsplit(".", 1)[1] for k in REPLACEMENTS})
    METADATA_DEPENDENCIES = (PositionProvider, QualifiedNameProvider)

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call):
        if not self.enabled("DS211"):
            return updated_node
        names = qnames(self, original_node.func)
        hit = next((n for n in names if n in REPLACEMENTS), None)
        if hit is None:
            return updated_node
        spec = REPLACEMENTS[hit]
        if not self.kb.target_at_least("3.0" if spec.get("add") else "2.4"):
            return updated_node
        module, cls = spec["new"]
        old_module, _, old_cls = hit.rpartition(".")
        args = []
        hook_items = []
        notes = []
        for a in updated_node.args:
            key = a.keyword.value if a.keyword is not None else None
            if key in spec["renames"]:
                args.append(a.with_changes(keyword=cst.Name(spec["renames"][key])))
                notes.append(f"{key}= -> {spec['renames'][key]}=")
            elif key in spec["hook_params"]:
                hook_items.append(cst.DictElement(cst.SimpleString(f'"{key}"'), a.value))
                notes.append(f"{key}= -> hook_params")
            elif key == "runtime_parameters":
                args.append(a)
                self.report("DS211", Severity.MANUAL, original_node,
                            "PostgresOperator(runtime_parameters=...) has no direct equivalent on "
                            "SQLExecuteQueryOperator; pass the settings through hook_params={'options': ...} "
                            "or SET statements in the SQL.")
            else:
                args.append(a)
        if hook_items:
            existing = kwarg(updated_node, "hook_params")
            if existing is not None:
                self.report("DS211", Severity.MANUAL, original_node,
                            "Both hook_params and connection settings are set; merge them by hand.")
            else:
                args.append(cst.Arg(keyword=cst.Name("hook_params"), value=cst.Dict(hook_items),
                                    equal=cst.AssignEqual(whitespace_before=cst.SimpleWhitespace(""),
                                                          whitespace_after=cst.SimpleWhitespace(""))))
        # keep commas tidy
        fixed = []
        for i, a in enumerate(args):
            if i < len(args) - 1 and not isinstance(a.comma, cst.Comma):
                a = a.with_changes(comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")))
            fixed.append(a)
        if fixed and len(fixed) != len(updated_node.args):
            fixed[-1] = fixed[-1].with_changes(comma=updated_node.args[-1].comma)
        for k, v in spec.get("add", {}).items():
            if kwarg(updated_node, k) is None:
                arg = cst.Arg(keyword=cst.Name(k), value=cst.parse_expression(v),
                              equal=cst.AssignEqual(whitespace_before=cst.SimpleWhitespace(""),
                                                    whitespace_after=cst.SimpleWhitespace("")))
                if fixed and not isinstance(fixed[-1].comma, cst.Comma):
                    fixed[-1] = fixed[-1].with_changes(comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")))
                    fixed.append(arg)
                elif fixed:
                    fixed.append(arg.with_changes(comma=fixed[-1].comma))
                    fixed[-2] = fixed[-2].with_changes(comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")))
                else:
                    fixed.append(arg)
                notes.append(f"{k}={v}")
        self.add_import(module, cls)
        self.remove_import_if_unused(old_module, old_cls)
        self.state.provider_modules.add(module)
        detail = f" ({', '.join(notes)})" if notes else ""
        why = ("is deprecated" if spec.get("add") else "no longer exists in current provider releases")
        self.report("DS211", Severity.FIXED, original_node,
                    f"{old_cls} {why}; replaced with {cls} from {module}{detail}.")
        return updated_node.with_changes(func=cst.Name(cls), args=fixed)
