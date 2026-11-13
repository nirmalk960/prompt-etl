"""Condition builder shared by filter, conditional column and validation ops."""

from __future__ import annotations

from typing import Any

from .base import OpError, Param, Schema, opts
from .sqlsafe import (
    dtype_of,
    ident,
    lit,
    resolve,
    split_list,
    typed_lit,
)

OPERATORS = opts(
    ("=", "equals"),
    ("!=", "does not equal"),
    (">", "greater than"),
    (">=", "greater than or equal"),
    ("<", "less than"),
    ("<=", "less than or equal"),
    ("between", "between"),
    ("in", "is one of (comma separated)"),
    ("not_in", "is not one of"),
    ("contains", "contains"),
    ("not_contains", "does not contain"),
    ("starts_with", "starts with"),
    ("ends_with", "ends with"),
    ("regex", "matches regex"),
    ("is_null", "is empty"),
    ("not_null", "is not empty"),
)

_NO_VALUE = {"is_null", "not_null"}

CONDITION_FIELDS: tuple[Param, ...] = (
    Param("column", "Column", "column", required=True),
    Param("operator", "Operator", "select", required=True, default="=", options=OPERATORS),
    Param(
        "value", "Value", "text",
        show_if=("operator", tuple(v for v, _ in OPERATORS if v not in _NO_VALUE)),
    ),
    Param("value2", "And", "text", show_if=("operator", ("between",))),
)


def condition_sql(cond: dict[str, Any], schema: Schema) -> str:
    col = resolve(cond.get("column"), schema)
    dtype = dtype_of(col, schema)
    op = str(cond.get("operator") or "=")
    c = ident(col)
    value = cond.get("value")

    if op == "is_null":
        return f"{c} IS NULL"
    if op == "not_null":
        return f"{c} IS NOT NULL"
    if op in {"=", "!=", ">", ">=", "<", "<="}:
        return f"{c} {op} {typed_lit(value, dtype, col)}"
    if op == "between":
        lo = typed_lit(value, dtype, col)
        hi = typed_lit(cond.get("value2"), dtype, col)
        return f"{c} BETWEEN {lo} AND {hi}"
    if op in {"in", "not_in"}:
        items = split_list(value)
        if not items:
            raise OpError(f"list of values for '{col}' is empty")
        inner = ", ".join(typed_lit(v, dtype, col) for v in items)
        return f"{c} {'NOT IN' if op == 'not_in' else 'IN'} ({inner})"
    text = f"lower(CAST({c} AS VARCHAR))"
    needle = lit(str(value if value is not None else "").lower())
    if op == "contains":
        return f"contains({text}, {needle})"
    if op == "not_contains":
        return f"NOT contains({text}, {needle})"
    if op == "starts_with":
        return f"starts_with({text}, {needle})"
    if op == "ends_with":
        return f"ends_with({text}, {needle})"
    if op == "regex":
        if not value:
            raise OpError("regex pattern is required")
        return f"regexp_matches(CAST({c} AS VARCHAR), {lit(str(value))})"
    raise OpError(f"unknown operator '{op}'")


def conditions_sql(conds: Any, schema: Schema, match: str = "all") -> str:
    if not isinstance(conds, list) or not conds:
        raise OpError("add at least one condition")
    parts = [f"({condition_sql(c, schema)})" for c in conds]
    joiner = " OR " if match == "any" else " AND "
    return joiner.join(parts)
