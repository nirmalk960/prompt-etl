"""Date operations."""

from __future__ import annotations

from typing import Any

from .base import OpError, OpSpec, Param, Schema, opts
from .registry import register
from .sqlsafe import dtype_of, ident, is_temporal, lit, names, new_name, resolve


def _as_timestamp(col: str, schema: Schema) -> str:
    if is_temporal(dtype_of(col, schema)):
        return f"CAST({ident(col)} AS TIMESTAMP)"
    return f"TRY_CAST({ident(col)} AS TIMESTAMP)"


def _parse_date(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    col = resolve(cfg.get("column"), schemas[0])
    target = "DATE" if (cfg.get("to") or "date") == "date" else "TIMESTAMP"
    fmt = (cfg.get("format") or "").strip()
    src = f"CAST({ident(col)} AS VARCHAR)"
    if fmt:
        e = f"CAST(try_strptime({src}, {lit(fmt)}) AS {target})"
    else:
        e = f"TRY_CAST({src} AS {target})"
    out = (cfg.get("new_column") or "").strip()
    if not out or out.lower() == col.lower():
        return f"SELECT * REPLACE ({e} AS {ident(col)}) FROM df"
    if out.lower() in {n.lower() for n in names(schemas[0])}:
        raise OpError(f"column '{out}' already exists")
    return f"SELECT *, {e} AS {ident(out)} FROM df"


register(OpSpec(
    id="parse_date", label="Parse dates", category="dates",
    description="Turn text into dates using a format (or automatic detection)",
    params=(
        Param("column", "Column", "column", required=True),
        Param("format", "Format", "text", placeholder="%d/%m/%Y",
              help="strftime codes, e.g. %Y-%m-%d, %d/%m/%Y %H:%M. Empty = automatic."),
        Param("to", "Result type", "select", default="date", options=opts("date", "timestamp")),
        Param("new_column", "New column", "text", help="Empty = replace the column"),
    ),
    compile=_parse_date,
))

_PARTS = {
    "year": ("year({t})", "year"),
    "quarter": ("quarter({t})", "quarter"),
    "month": ("month({t})", "month"),
    "month_name": ("monthname({t})", "month_name"),
    "year_month": ("strftime({t}, '%Y-%m')", "year_month"),
    "week": ("weekofyear({t})", "week"),
    "day": ("day({t})", "day"),
    "weekday": ("isodow({t})", "weekday"),
    "weekday_name": ("dayname({t})", "weekday_name"),
    "hour": ("hour({t})", "hour"),
    "date": ("CAST({t} AS DATE)", "date"),
}


def _date_parts(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    col = resolve(cfg.get("column"), schemas[0])
    parts = cfg.get("parts") or []
    if not parts:
        raise OpError("choose at least one date part")
    t = _as_timestamp(col, schemas[0])
    existing = {n.lower() for n in names(schemas[0])}
    out = []
    for p in parts:
        if p not in _PARTS:
            raise OpError(f"unknown date part '{p}'")
        expr, suffix = _PARTS[p]
        name = f"{col}_{suffix}"
        if name.lower() in existing:
            raise OpError(f"column '{name}' already exists")
        out.append(f"{expr.format(t=t)} AS {ident(name)}")
    return f"SELECT *, {', '.join(out)} FROM df"


register(OpSpec(
    id="date_parts", label="Date parts", category="dates",
    description="Add year, quarter, month, week, weekday... columns from a date",
    params=(
        Param("column", "Date column", "column", required=True),
        Param("parts", "Parts", "multiselect", required=True, default=["year", "month"],
              options=opts(*_PARTS.keys())),
    ),
    compile=_date_parts,
))

_UNITS = ("day", "week", "month", "quarter", "year", "hour", "minute", "second")


def _date_diff(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    start = resolve(cfg.get("start"), schemas[0], "start column")
    end = resolve(cfg.get("end"), schemas[0], "end column")
    unit = cfg.get("unit") or "day"
    if unit not in _UNITS:
        raise OpError(f"unknown unit '{unit}'")
    name = new_name(cfg.get("new_column"))
    if name.lower() in {n.lower() for n in names(schemas[0])}:
        raise OpError(f"column '{name}' already exists")
    e = f"date_diff({lit(unit)}, {_as_timestamp(start, schemas[0])}, {_as_timestamp(end, schemas[0])})"
    return f"SELECT *, {e} AS {ident(name)} FROM df"


register(OpSpec(
    id="date_diff", label="Date difference", category="dates",
    description="Days (or other units) between two date columns",
    params=(
        Param("start", "Start date", "column", required=True),
        Param("end", "End date", "column", required=True),
        Param("unit", "Unit", "select", default="day", options=opts(*_UNITS)),
        Param("new_column", "New column", "text", required=True, placeholder="days_to_ship"),
    ),
    compile=_date_diff,
))
