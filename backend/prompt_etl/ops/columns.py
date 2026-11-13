"""Column operations."""

from __future__ import annotations

from typing import Any

from .base import OpError, OpSpec, Param, Schema, opts
from .conditions import CONDITION_FIELDS, condition_sql
from .registry import register
from .sqlsafe import auto_lit, ident, lit, names, new_name, resolve, resolve_all, safe_expr


def _select(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    cols = resolve_all(cfg.get("columns"), schemas[0])
    if not cols:
        raise OpError("choose at least one column to keep")
    return f"SELECT {', '.join(ident(c) for c in cols)} FROM df"


register(OpSpec(
    id="select", label="Select columns", category="columns",
    description="Keep only the chosen columns, in that order",
    params=(Param("columns", "Columns to keep", "columns", required=True),),
    compile=_select, preset_prompt="Keep columns: ",
))


def _drop_columns(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    cols = resolve_all(cfg.get("columns"), schemas[0])
    if not cols:
        raise OpError("choose at least one column to drop")
    if len(cols) >= len(schemas[0]):
        raise OpError("cannot drop every column")
    return f"SELECT * EXCLUDE ({', '.join(ident(c) for c in cols)}) FROM df"


register(OpSpec(
    id="drop_columns", label="Drop columns", category="columns",
    description="Remove the chosen columns",
    params=(Param("columns", "Columns to drop", "columns", required=True),),
    compile=_drop_columns, preset_prompt="Drop columns ",
))


def _rename(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    items = cfg.get("renames") or []
    if not items:
        raise OpError("add at least one column to rename")
    pairs: list[str] = []
    targets: set[str] = set()
    for it in items:
        old = resolve(it.get("from"), schemas[0])
        new = new_name(it.get("to"))
        if new.lower() in targets:
            raise OpError(f"two columns renamed to '{new}'")
        targets.add(new.lower())
        pairs.append(f"{ident(old)} AS {ident(new)}")
    renamed = {resolve(it.get("from"), schemas[0]).lower() for it in items}
    clash = [n for n in names(schemas[0]) if n.lower() in targets and n.lower() not in renamed]
    if clash:
        raise OpError(f"column '{clash[0]}' already exists")
    return f"SELECT * RENAME ({', '.join(pairs)}) FROM df"


register(OpSpec(
    id="rename", label="Rename columns", category="columns",
    description="Rename one or more columns",
    params=(Param("renames", "Renames", "list", required=True, fields=(
        Param("from", "Column", "column", required=True),
        Param("to", "New name", "text", required=True),
    )),),
    compile=_rename, preset_prompt="Rename column  to ",
))

_CAST_TYPES = {
    "integer": "BIGINT",
    "number": "DOUBLE",
    "text": "VARCHAR",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
}


def _cast(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    items = cfg.get("casts") or []
    if not items:
        raise OpError("add at least one column to convert")
    strict = cfg.get("on_error") == "fail"
    fn = "CAST" if strict else "TRY_CAST"
    parts = []
    for it in items:
        col = resolve(it.get("column"), schemas[0])
        to = _CAST_TYPES.get(str(it.get("to")))
        if to is None:
            raise OpError(f"choose a target type for '{col}'")
        parts.append(f"{fn}({ident(col)} AS {to}) AS {ident(col)}")
    return f"SELECT * REPLACE ({', '.join(parts)}) FROM df"


register(OpSpec(
    id="cast", label="Change type", category="columns",
    description="Convert columns to integer, number, text, boolean, date or timestamp",
    params=(
        Param("casts", "Conversions", "list", required=True, fields=(
            Param("column", "Column", "column", required=True),
            Param("to", "To type", "select", required=True, default="number",
                  options=opts(*_CAST_TYPES.keys())),
        )),
        Param("on_error", "Values that can't convert", "select", default="null",
              options=opts(("null", "become empty"), ("fail", "fail the step"))),
    ),
    compile=_cast, preset_prompt="Convert column  to ",
))


def _with_column(name: str, expr: str, schema: Schema) -> str:
    existing = {n.lower(): n for n in names(schema)}
    if name.lower() in existing:
        return f"SELECT * REPLACE (({expr}) AS {ident(existing[name.lower()])}) FROM df"
    return f"SELECT *, ({expr}) AS {ident(name)} FROM df"


def _formula(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    name = new_name(cfg.get("new_column"))
    expr = safe_expr(cfg.get("expression"), schemas[0])
    return _with_column(name, expr, schemas[0])


register(OpSpec(
    id="formula", label="Formula column", category="columns",
    description="Add or replace a column computed with a SQL expression",
    params=(
        Param("new_column", "Column name", "text", required=True, placeholder="net_total"),
        Param("expression", "Expression", "expression", required=True,
              placeholder="order_total - coalesce(discount_amount, 0)",
              help="SQL expression over this row, e.g. price * quantity, upper(region), "
                   "round(amount / 1000, 1). Quote names with spaces: \"Order Total\"."),
    ),
    compile=_formula, preset_prompt="Add a column  equal to ",
))


def _conditional(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    name = new_name(cfg.get("new_column"))
    rules = cfg.get("rules") or []
    if not rules:
        raise OpError("add at least one rule")
    whens = [
        f"WHEN {condition_sql(r, schemas[0])} THEN {auto_lit(r.get('then'))}" for r in rules
    ]
    expr = f"CASE {' '.join(whens)} ELSE {auto_lit(cfg.get('else_value'))} END"
    return _with_column(name, expr, schemas[0])


register(OpSpec(
    id="conditional_column", label="If / else column", category="columns",
    description="Set a column's value from rules checked in order",
    params=(
        Param("new_column", "Column name", "text", required=True, placeholder="order_size"),
        Param("rules", "Rules (first match wins)", "list", required=True,
              fields=CONDITION_FIELDS + (Param("then", "Then value", "text", required=True),)),
        Param("else_value", "Otherwise", "text", help="Leave empty for no value"),
    ),
    compile=_conditional,
))


def _concat(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    cols = resolve_all(cfg.get("columns"), schemas[0])
    if len(cols) < 2:
        raise OpError("choose at least two columns to combine")
    name = new_name(cfg.get("new_column"))
    sep = cfg.get("separator")
    sep = " " if sep is None else str(sep)
    expr = f"concat_ws({lit(sep)}, {', '.join(f'CAST({ident(c)} AS VARCHAR)' for c in cols)})"
    return _with_column(name, expr, schemas[0])


register(OpSpec(
    id="concat_columns", label="Combine columns", category="columns",
    description="Join several columns into one text column",
    params=(
        Param("columns", "Columns", "columns", required=True),
        Param("separator", "Separator", "text", default=" "),
        Param("new_column", "New column", "text", required=True, placeholder="full_name"),
    ),
    compile=_concat,
))
