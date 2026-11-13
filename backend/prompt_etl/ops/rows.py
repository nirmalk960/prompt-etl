"""Row operations. ``input_0_rn`` carries ``__rn`` (original row order)."""

from __future__ import annotations

from typing import Any

from .base import OpError, OpSpec, Param, Schema, opts
from .conditions import CONDITION_FIELDS, conditions_sql
from .registry import register
from .sqlsafe import (
    dtype_of,
    ident,
    is_numeric,
    names,
    resolve,
    resolve_all,
    to_int,
    to_number,
    typed_lit,
)

_MATCH = Param("match", "Keep rows that match", "select", default="all",
               options=opts(("all", "all conditions (AND)"), ("any", "any condition (OR)")))


def _filter(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    where = conditions_sql(cfg.get("conditions"), schemas[0], str(cfg.get("match") or "all"))
    return f"SELECT * FROM df WHERE {where}"


register(OpSpec(
    id="filter", label="Filter rows", category="rows",
    description="Keep rows that match conditions",
    params=(Param("conditions", "Conditions", "list", required=True, fields=CONDITION_FIELDS), _MATCH),
    compile=_filter, preset_prompt="Filter rows where ",
))


def _drop_nulls(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    cols = resolve_all(cfg.get("columns"), schemas[0]) or names(schemas[0])
    checks = [f"{ident(c)} IS NOT NULL" for c in cols]
    if cfg.get("how") == "all":
        return f"SELECT * FROM df WHERE {' OR '.join(checks)}"
    return f"SELECT * FROM df WHERE {' AND '.join(checks)}"


register(OpSpec(
    id="drop_nulls", label="Drop empty rows", category="rows",
    description="Remove rows with missing values",
    params=(
        Param("columns", "Check columns", "columns", help="Leave empty to check every column"),
        Param("how", "Drop a row when", "select", default="any",
              options=opts(("any", "any checked column is empty"), ("all", "all checked columns are empty"))),
    ),
    compile=_drop_nulls, preset_prompt="Drop rows with null values in ",
))

_FILL_METHODS = opts(
    ("value", "a fixed value"), ("mean", "column average"), ("median", "column median"),
    ("mode", "most common value"), ("previous", "previous row's value"), ("next", "next row's value"),
)


def _fill_nulls(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    schema = schemas[0]
    items = cfg.get("fills") or []
    if not items:
        raise OpError("add at least one column to fill")
    order = resolve(cfg["order_by"], schema) if cfg.get("order_by") else None
    order_sql = ident(order) if order else "__rn"
    parts = []
    for it in items:
        col = resolve(it.get("column"), schema)
        dtype = dtype_of(col, schema)
        c = ident(col)
        method = it.get("method") or "value"
        if method == "value":
            fill = typed_lit(it.get("value"), dtype, col)
        elif method in {"mean", "median"}:
            if not is_numeric(dtype):
                raise OpError(f"'{col}' is not numeric; {method} needs numbers")
            fill = f"(SELECT {'avg' if method == 'mean' else 'median'}({c}) FROM input_0)"
        elif method == "mode":
            fill = f"(SELECT mode({c}) FROM input_0)"
        elif method == "previous":
            fill = (f"last_value({c} IGNORE NULLS) OVER (ORDER BY {order_sql} "
                    "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)")
        elif method == "next":
            fill = (f"first_value({c} IGNORE NULLS) OVER (ORDER BY {order_sql} "
                    "ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)")
        else:
            raise OpError(f"unknown fill method '{method}'")
        parts.append(f"COALESCE({c}, {fill}) AS {c}")
    return (f"SELECT * EXCLUDE (__rn) REPLACE ({', '.join(parts)}) "
            f"FROM input_0_rn ORDER BY {order_sql}")


register(OpSpec(
    id="fill_nulls", label="Fill empty values", category="rows",
    description="Replace missing values with a value, statistic, or neighbouring row",
    params=(
        Param("fills", "Columns to fill", "list", required=True, fields=(
            Param("column", "Column", "column", required=True),
            Param("method", "Fill with", "select", required=True, default="value", options=_FILL_METHODS),
            Param("value", "Value", "text", show_if=("method", ("value",))),
        )),
        Param("order_by", "Row order for previous/next", "column",
              help="Defaults to the original row order"),
    ),
    compile=_fill_nulls, preset_prompt="Fill null values in  with ",
))


def _dedupe(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    keys = resolve_all(cfg.get("columns"), schemas[0])
    if not keys:
        return "SELECT DISTINCT * FROM df"
    order = resolve(cfg["order_by"], schemas[0]) if cfg.get("order_by") else None
    direction = "DESC" if cfg.get("keep") == "last" else "ASC"
    order_sql = f"{ident(order) if order else '__rn'} {direction}"
    part = ", ".join(ident(k) for k in keys)
    return (f"SELECT * EXCLUDE (__rn) FROM input_0_rn "
            f"QUALIFY row_number() OVER (PARTITION BY {part} ORDER BY {order_sql}) = 1 ORDER BY __rn")


register(OpSpec(
    id="deduplicate", label="Remove duplicates", category="rows",
    description="Keep one row per unique key (or remove fully identical rows)",
    params=(
        Param("columns", "Key columns", "columns", help="Leave empty to compare whole rows"),
        Param("keep", "Keep", "select", default="first", options=opts("first", "last")),
        Param("order_by", "Ordered by", "column", help="Defaults to the original row order"),
    ),
    compile=_dedupe, preset_prompt="Remove duplicate rows based on ",
))


def _sort(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    items = cfg.get("sorts") or []
    if not items:
        raise OpError("add at least one sort column")
    parts = []
    for it in items:
        col = resolve(it.get("column"), schemas[0])
        direction = "DESC" if it.get("direction") == "desc" else "ASC"
        nulls = "NULLS FIRST" if it.get("nulls") == "first" else "NULLS LAST"
        parts.append(f"{ident(col)} {direction} {nulls}")
    return f"SELECT * FROM df ORDER BY {', '.join(parts)}"


register(OpSpec(
    id="sort", label="Sort", category="rows",
    description="Order rows by one or more columns",
    params=(Param("sorts", "Sort by", "list", required=True, fields=(
        Param("column", "Column", "column", required=True),
        Param("direction", "Direction", "select", default="asc",
              options=opts(("asc", "ascending"), ("desc", "descending"))),
        Param("nulls", "Empty values", "select", default="last", options=opts("last", "first")),
    )),),
    compile=_sort, preset_prompt="Sort by  ascending",
))


def _top_n(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    n = to_int(cfg.get("count", 10), "number of rows", minimum=0)
    offset = to_int(cfg.get("offset") or 0, "skip rows", minimum=0)
    if cfg.get("order_by"):
        col = resolve(cfg["order_by"], schemas[0])
        direction = "ASC" if cfg.get("direction") == "asc" else "DESC"
        order = f"ORDER BY {ident(col)} {direction} NULLS LAST"
        src = "df"
        select = "*"
    else:
        order = "ORDER BY __rn"
        src = "input_0_rn"
        select = "* EXCLUDE (__rn)"
    return f"SELECT {select} FROM {src} {order} LIMIT {n} OFFSET {offset}"


register(OpSpec(
    id="top_n", label="Top N / limit", category="rows",
    description="Keep the first N rows, optionally the top N by a column",
    params=(
        Param("count", "Number of rows", "integer", required=True, default=10),
        Param("order_by", "Top by column", "column", help="Leave empty to keep the first rows"),
        Param("direction", "Highest or lowest", "select", default="desc",
              options=opts(("desc", "highest first"), ("asc", "lowest first"))),
        Param("offset", "Skip rows", "integer", default=0),
    ),
    compile=_top_n,
))


def _sample(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    seed = to_int(cfg.get("seed", 42), "seed", minimum=0)
    size = to_number(cfg.get("size", 10), "sample size")
    if cfg.get("method") == "percent":
        if not 0 < size <= 100:
            raise OpError("percent must be between 0 and 100")
        return f"SELECT * FROM df USING SAMPLE {float(size)}% (bernoulli, {seed})"
    n = to_int(size, "sample size", minimum=1)
    return f"SELECT * FROM df USING SAMPLE reservoir({n} ROWS) REPEATABLE ({seed})"


register(OpSpec(
    id="sample", label="Sample rows", category="rows",
    description="Random sample by row count or percent (repeatable with a seed)",
    params=(
        Param("method", "Sample by", "select", default="rows", options=opts(("rows", "row count"), "percent")),
        Param("size", "Size", "number", required=True, default=1000),
        Param("seed", "Seed", "integer", default=42),
    ),
    compile=_sample,
))
