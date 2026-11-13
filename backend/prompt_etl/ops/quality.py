"""Data quality operations."""

from __future__ import annotations

from typing import Any

from .base import OpError, OpSpec, Param, Schema, opts
from .registry import register
from .sqlsafe import (
    dtype_of,
    ident,
    is_numeric,
    lit,
    names,
    resolve,
    resolve_all,
    split_list,
    typed_lit,
)


def _profile(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    schema = schemas[0]
    cols = resolve_all(cfg.get("columns"), schema) or names(schema)
    parts = []
    for i, col in enumerate(cols):
        dtype = dtype_of(col, schema)
        c = ident(col)
        numeric = is_numeric(dtype)
        mean = f"avg({c})::DOUBLE" if numeric else "NULL::DOUBLE"
        std = f"stddev_samp({c})::DOUBLE" if numeric else "NULL::DOUBLE"
        median = f"median({c})::DOUBLE" if numeric else "NULL::DOUBLE"
        parts.append(
            f"SELECT {i} AS __pos, {lit(col)} AS column_name, {lit(dtype)} AS column_type, "
            f"count(*) AS row_count, count(*) - count({c}) AS null_count, "
            f"round(100.0 * (count(*) - count({c})) / nullif(count(*), 0), 2) AS null_pct, "
            f"approx_count_distinct({c}) AS distinct_count, "
            f"CAST(min({c}) AS VARCHAR) AS min_value, CAST(max({c}) AS VARCHAR) AS max_value, "
            f"{mean} AS mean, {median} AS median, {std} AS std, "
            f"CAST(mode({c}) AS VARCHAR) AS most_common FROM df"
        )
    return f"SELECT * EXCLUDE (__pos) FROM ({' UNION ALL '.join(parts)}) ORDER BY __pos"


register(OpSpec(
    id="profile", label="Profile data", category="quality",
    description="One row per column: type, empties, distinct values, min/max, mean...",
    params=(Param("columns", "Columns", "columns", help="Empty = all columns"),),
    compile=_profile,
))

_CHECKS = opts(
    ("not_null", "is not empty"), ("unique", "is unique"), ("between", "is between"),
    ("min", "is at least"), ("max", "is at most"), ("in_list", "is one of"),
    ("regex", "matches regex"),
)


def _rule_ok(rule: dict[str, Any], schema: Schema) -> tuple[str, str]:
    col = resolve(rule.get("column"), schema)
    dtype = dtype_of(col, schema)
    c = ident(col)
    check = rule.get("check") or "not_null"
    v = rule.get("value")
    if check == "not_null":
        return f"{c} IS NOT NULL", f"{col} is empty"
    if check == "unique":
        return f"({c} IS NULL OR count(*) OVER (PARTITION BY {c}) = 1)", f"{col} is duplicated"
    if check == "between":
        lo, hi = typed_lit(v, dtype, col), typed_lit(rule.get("value2"), dtype, col)
        return f"({c} IS NULL OR {c} BETWEEN {lo} AND {hi})", f"{col} out of range"
    if check == "min":
        return f"({c} IS NULL OR {c} >= {typed_lit(v, dtype, col)})", f"{col} below minimum"
    if check == "max":
        return f"({c} IS NULL OR {c} <= {typed_lit(v, dtype, col)})", f"{col} above maximum"
    if check == "in_list":
        items = split_list(v)
        if not items:
            raise OpError(f"allowed values for '{col}' are empty")
        inner = ", ".join(typed_lit(x, dtype, col) for x in items)
        return f"({c} IS NULL OR {c} IN ({inner}))", f"{col} not allowed"
    if check == "regex":
        if not v:
            raise OpError("regex pattern is required")
        return (f"({c} IS NULL OR regexp_full_match(CAST({c} AS VARCHAR), {lit(str(v))}))",
                f"{col} bad format")
    raise OpError(f"unknown check '{check}'")


def _flagged(cfg: dict[str, Any], schema: Schema) -> tuple[str, list[str]]:
    rules = cfg.get("rules") or []
    if not rules:
        raise OpError("add at least one rule")
    oks = [_rule_ok(r, schema) for r in rules]
    valid = " AND ".join(f"coalesce({ok}, TRUE)" for ok, _ in oks)
    errors = ", ".join(f"CASE WHEN NOT coalesce({ok}, TRUE) THEN {lit(msg)} END" for ok, msg in oks)
    sql = (f"SELECT *, ({valid}) AS _valid, nullif(concat_ws('; ', {errors}), '') AS _errors "
           f"FROM df")
    return sql, [msg for _, msg in oks]


def _validate(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    flagged, _ = _flagged(cfg, schemas[0])
    action = cfg.get("on_fail") or "flag"
    if action == "drop":
        return f"SELECT * EXCLUDE (_valid, _errors) FROM ({flagged}) WHERE _valid"
    if action == "fail":
        return "SELECT * FROM df"
    return flagged


def _validate_check(con: Any, cfg: dict[str, Any], schemas: list[Schema]) -> None:
    if (cfg.get("on_fail") or "flag") != "fail":
        return
    flagged, _ = _flagged(cfg, schemas[0])
    bad, sample = con.execute(
        f"SELECT count(*), any_value(_errors) FROM ({flagged}) WHERE NOT _valid"
    ).fetchone()
    if bad:
        raise OpError(f"validation failed: {bad} row(s) broke the rules (e.g. {sample})")


register(OpSpec(
    id="validate", label="Validate rules", category="quality",
    description="Check rules per row: flag, drop, or fail the pipeline",
    params=(
        Param("rules", "Rules", "list", required=True, fields=(
            Param("column", "Column", "column", required=True),
            Param("check", "Check", "select", required=True, default="not_null", options=_CHECKS),
            Param("value", "Value", "text", show_if=("check", ("between", "min", "max", "in_list", "regex"))),
            Param("value2", "And", "text", show_if=("check", ("between",))),
        )),
        Param("on_fail", "When a row breaks a rule", "select", default="flag",
              options=opts(("flag", "add _valid and _errors columns"), ("drop", "drop the row"),
                           ("fail", "fail the pipeline"))),
    ),
    compile=_validate, post_check=_validate_check,
))
