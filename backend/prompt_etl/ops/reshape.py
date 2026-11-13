"""Reshape operations."""

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
    new_name,
    resolve,
    resolve_all,
    split_list,
    to_int,
    to_number,
)

_AGG_FNS = {
    "sum": "sum({c})",
    "avg": "avg({c})",
    "min": "min({c})",
    "max": "max({c})",
    "count": "count({c})",
    "count_distinct": "count(DISTINCT {c})",
    "median": "median({c})",
    "std": "stddev_samp({c})",
    "first": "first({c})",
    "last": "last({c})",
}
_NUMERIC_ONLY = {"sum", "avg", "median", "std"}
AGG_OPTIONS = opts(
    "sum", ("avg", "average"), "min", "max", "count", ("count_distinct", "count distinct"),
    "median", ("std", "standard deviation"), "first", "last",
)


def _agg_expr(fn: str, col: str | None, schema: Schema) -> str:
    if fn not in _AGG_FNS:
        raise OpError(f"unknown function '{fn}'")
    if col is None:
        if fn != "count":
            raise OpError(f"{fn} needs a column")
        return "count(*)"
    if fn in _NUMERIC_ONLY and not is_numeric(dtype_of(col, schema)):
        raise OpError(f"'{col}' is not numeric; {fn} needs numbers")
    return _AGG_FNS[fn].format(c=ident(col))


def _aggregate(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    schema = schemas[0]
    groups = resolve_all(cfg.get("group_by"), schema)
    metrics = cfg.get("metrics") or []
    if not metrics:
        raise OpError("add at least one metric")
    select = [ident(g) for g in groups]
    used = {g.lower() for g in groups}
    for m in metrics:
        fn = m.get("function") or "sum"
        col = resolve(m["column"], schema) if m.get("column") else None
        alias = (m.get("alias") or "").strip() or (f"{fn}_{col}" if col else "row_count")
        if alias.lower() in used:
            raise OpError(f"output column '{alias}' is used twice")
        used.add(alias.lower())
        select.append(f"{_agg_expr(fn, col, schema)} AS {ident(alias)}")
    sql = f"SELECT {', '.join(select)} FROM df"
    if groups:
        part = ", ".join(ident(g) for g in groups)
        sql += f" GROUP BY {part} ORDER BY {part}"
    return sql


register(OpSpec(
    id="aggregate", label="Group and aggregate", category="reshape",
    description="Group rows and compute sums, averages, counts...",
    params=(
        Param("group_by", "Group by", "columns", help="Leave empty for one total row"),
        Param("metrics", "Metrics", "list", required=True, fields=(
            Param("function", "Function", "select", required=True, default="sum", options=AGG_OPTIONS),
            Param("column", "Column", "column", help="Empty with count = count rows"),
            Param("alias", "Output name", "text", placeholder="total_revenue"),
        )),
    ),
    compile=_aggregate, preset_prompt="Group by  and calculate sum() as ",
))


def _pivot(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    schema = schemas[0]
    index = resolve_all(cfg.get("index"), schema)
    on = resolve(cfg.get("pivot_column"), schema, "pivot column")
    value = resolve(cfg.get("value_column"), schema, "value column")
    fn = cfg.get("function") or "sum"
    agg = _agg_expr(fn, value, schema)
    sql = f"PIVOT df ON {ident(on)} USING {agg}"
    if index:
        sql += f" GROUP BY {', '.join(ident(i) for i in index)}"
    return sql


register(OpSpec(
    id="pivot", label="Pivot", category="reshape", dynamic_schema=True,
    description="Turn the values of one column into columns",
    params=(
        Param("index", "Row labels", "columns"),
        Param("pivot_column", "Column whose values become columns", "column", required=True),
        Param("value_column", "Values", "column", required=True),
        Param("function", "Combine with", "select", default="sum", options=AGG_OPTIONS),
    ),
    compile=_pivot,
))


def _unpivot(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    schema = schemas[0]
    ids = resolve_all(cfg.get("id_columns"), schema)
    values = resolve_all(cfg.get("value_columns"), schema) or [n for n in names(schema) if n not in ids]
    if not values:
        raise OpError("no columns left to unpivot")
    name_col = new_name(cfg.get("name_column") or "variable", "name column")
    value_col = new_name(cfg.get("value_column") or "value", "value column")
    types = {dtype_of(v, schema) for v in values}
    cols = ", ".join(
        ident(v) if len(types) == 1 else f"CAST({ident(v)} AS VARCHAR) AS {ident(v)}" for v in values
    )
    keep = ", ".join(ident(i) for i in ids)
    src = f"(SELECT {keep + ', ' if keep else ''}{cols} FROM df)"
    return (f"UNPIVOT {src} ON {', '.join(ident(v) for v in values)} "
            f"INTO NAME {ident(name_col)} VALUE {ident(value_col)}")


register(OpSpec(
    id="unpivot", label="Unpivot", category="reshape",
    description="Turn columns into name/value rows (wide to long)",
    params=(
        Param("id_columns", "Keep as identifiers", "columns"),
        Param("value_columns", "Columns to unpivot", "columns", help="Empty = all other columns"),
        Param("name_column", "Name column", "text", default="variable"),
        Param("value_column", "Value column", "text", default="value"),
    ),
    compile=_unpivot,
))

_WINDOW_FNS = opts(
    ("row_number", "row number"), "rank", ("dense_rank", "dense rank"),
    ("percent_rank", "percent rank"), ("running_sum", "running total"),
    ("running_avg", "running average"), "lag", "lead",
    ("moving_avg", "moving average"), ("moving_sum", "moving sum"),
    ("pct_of_total", "percent of total"),
)
_NEEDS_COLUMN = {"running_sum", "running_avg", "lag", "lead", "moving_avg", "moving_sum", "pct_of_total"}
_NEEDS_ORDER = {"row_number", "rank", "dense_rank", "percent_rank", "running_sum", "running_avg",
                "lag", "lead", "moving_avg", "moving_sum"}


def _window(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    schema = schemas[0]
    fn = cfg.get("function") or "row_number"
    part = resolve_all(cfg.get("partition_by"), schema)
    order = resolve(cfg["order_by"], schema) if cfg.get("order_by") else None
    if fn in _NEEDS_ORDER and order is None:
        raise OpError(f"{fn.replace('_', ' ')} needs an order-by column")
    col = resolve(cfg["column"], schema) if cfg.get("column") else None
    if fn in _NEEDS_COLUMN and col is None:
        raise OpError(f"{fn.replace('_', ' ')} needs a value column")
    if fn in {"running_sum", "running_avg", "moving_avg", "moving_sum", "pct_of_total"}:
        assert col is not None
        if not is_numeric(dtype_of(col, schema)):
            raise OpError(f"'{col}' is not numeric")
    direction = "DESC" if cfg.get("direction") == "desc" else "ASC"
    over_parts = []
    if part:
        over_parts.append("PARTITION BY " + ", ".join(ident(p) for p in part))
    if order:
        over_parts.append(f"ORDER BY {ident(order)} {direction}")
    over = " ".join(over_parts)
    n = to_int(cfg.get("size") or (3 if fn.startswith("moving") else 1), "offset / window size", minimum=1)
    c = ident(col) if col else ""
    exprs = {
        "row_number": f"row_number() OVER ({over})",
        "rank": f"rank() OVER ({over})",
        "dense_rank": f"dense_rank() OVER ({over})",
        "percent_rank": f"percent_rank() OVER ({over})",
        "running_sum": f"sum({c}) OVER ({over} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
        "running_avg": f"avg({c}) OVER ({over} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
        "lag": f"lag({c}, {n}) OVER ({over})",
        "lead": f"lead({c}, {n}) OVER ({over})",
        "moving_avg": f"avg({c}) OVER ({over} ROWS BETWEEN {n - 1} PRECEDING AND CURRENT ROW)",
        "moving_sum": f"sum({c}) OVER ({over} ROWS BETWEEN {n - 1} PRECEDING AND CURRENT ROW)",
        "pct_of_total": f"100.0 * {c} / nullif(sum({c}) OVER ({' '.join(over_parts[:1])}), 0)",
    }
    if fn not in exprs:
        raise OpError(f"unknown window function '{fn}'")
    name = new_name(cfg.get("new_column") or (f"{fn}_{col}" if col else fn))
    if name.lower() in {x.lower() for x in names(schema)}:
        raise OpError(f"column '{name}' already exists")
    sql = f"SELECT *, {exprs[fn]} AS {ident(name)} FROM df"
    if order:
        sort = [ident(p) for p in part] + [f"{ident(order)} {direction}"]
        sql += f" ORDER BY {', '.join(sort)}"
    return sql


register(OpSpec(
    id="window", label="Window / running calc", category="reshape",
    description="Rank, running totals, lag/lead and moving averages",
    params=(
        Param("function", "Calculation", "select", required=True, default="running_sum", options=_WINDOW_FNS),
        Param("column", "Value column", "column",
              show_if=("function", tuple(sorted(_NEEDS_COLUMN)))),
        Param("partition_by", "Restart for each", "columns", help="e.g. per customer or region"),
        Param("order_by", "Order by", "column"),
        Param("direction", "Order", "select", default="asc",
              options=opts(("asc", "ascending"), ("desc", "descending"))),
        Param("size", "Offset / window size", "integer",
              show_if=("function", ("lag", "lead", "moving_avg", "moving_sum"))),
        Param("new_column", "New column", "text"),
    ),
    compile=_window,
))


def _bin(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    schema = schemas[0]
    col = resolve(cfg.get("column"), schema)
    if not is_numeric(dtype_of(col, schema)):
        raise OpError(f"'{col}' is not numeric")
    c = ident(col)
    method = cfg.get("method") or "equal_width"
    labels = split_list(cfg.get("labels"))
    name = new_name(cfg.get("new_column") or f"{col}_bin")
    if name.lower() in {x.lower() for x in names(schema)}:
        raise OpError(f"column '{name}' already exists")

    if method == "custom":
        edges = [to_number(e, "edge") for e in split_list(cfg.get("edges"))]
        if len(edges) < 2 or sorted(edges) != edges:
            raise OpError("edges must be at least two increasing numbers, e.g. 0, 50, 100")
        n = len(edges) - 1
        if labels and len(labels) != n:
            raise OpError(f"{n} bins need {n} labels")
        whens = []
        for i in range(n):
            lo, hi = edges[i], edges[i + 1]
            upper = "<=" if i == n - 1 else "<"
            label = labels[i] if labels else f"{lo} - {hi}"
            whens.append(f"WHEN {c} >= {lit(lo)} AND {c} {upper} {lit(hi)} THEN {lit(label)}")
        return f"SELECT *, CASE {' '.join(whens)} END AS {ident(name)} FROM df"

    n = to_int(cfg.get("bins") or 5, "number of bins", minimum=1)
    if labels and len(labels) != n:
        raise OpError(f"{n} bins need {n} labels")
    label_list = "[" + ", ".join(lit(lbl) for lbl in labels) + "]"
    if method == "quantile":
        idx = f"CASE WHEN {c} IS NULL THEN NULL ELSE ntile({n}) OVER (ORDER BY {c}) - 1 END"
        label_expr = (f"list_extract({label_list}, CAST(__b AS INTEGER) + 1)" if labels
                      else "'Q' || CAST(__b + 1 AS VARCHAR)")
        return (f"SELECT * EXCLUDE (__b), {label_expr} AS {ident(name)} FROM "
                f"(SELECT *, {idx} AS __b FROM df)")
    if method != "equal_width":
        raise OpError(f"unknown method '{method}'")
    width = f"((s.__mx - s.__mn) / {n})"
    idx = f"coalesce(least(CAST(floor(({c} - s.__mn) / nullif({width}, 0)) AS BIGINT), {n - 1}), 0)"
    if labels:
        label_expr = f"list_extract({label_list}, CAST({idx} AS INTEGER) + 1)"
    else:
        lo = f"(s.__mn + {idx} * {width})"
        label_expr = f"printf('%.2f - %.2f', {lo}, {lo} + {width})"
    return (f"SELECT d.*, CASE WHEN {c} IS NULL THEN NULL ELSE {label_expr} END "
            f"AS {ident(name)} FROM df AS d, (SELECT min({c}) AS __mn, max({c}) AS __mx FROM df) AS s")


register(OpSpec(
    id="bin_column", label="Bin / bucket", category="reshape",
    description="Group a number column into ranges",
    params=(
        Param("column", "Column", "column", required=True),
        Param("method", "Method", "select", default="equal_width",
              options=opts(("equal_width", "equal width"), ("quantile", "equal count (quantiles)"),
                           ("custom", "custom edges"))),
        Param("bins", "Number of bins", "integer", default=5, show_if=("method", ("equal_width", "quantile"))),
        Param("edges", "Edges", "text", placeholder="0, 50, 100, 500", show_if=("method", ("custom",))),
        Param("labels", "Labels (optional, comma separated)", "text", placeholder="low, medium, high"),
        Param("new_column", "New column", "text"),
    ),
    compile=_bin,
))
