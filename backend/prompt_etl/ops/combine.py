"""Two-input operations: ``input_0`` is the left input, ``input_1`` the right."""

from __future__ import annotations

from typing import Any

from .base import OpError, OpSpec, Param, Schema, opts
from .registry import register
from .sqlsafe import ident, names, resolve

_HOW = opts(
    ("inner", "inner: matching rows only"),
    ("left", "left: all left rows"),
    ("right", "right: all right rows"),
    ("full", "full: all rows from both"),
    ("semi", "semi: left rows that have a match"),
    ("anti", "anti: left rows with no match"),
)


def _join(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    left, right = schemas[0], schemas[1]
    keys = cfg.get("keys") or []
    if not keys:
        raise OpError("add at least one pair of key columns")
    pairs = [
        (resolve(k.get("left"), left, "left key"), resolve(k.get("right"), right, "right key"))
        for k in keys
    ]
    on = " AND ".join(f"l.{ident(a)} = r.{ident(b)}" for a, b in pairs)
    how = cfg.get("how") or "inner"
    if how in {"semi", "anti"}:
        return f"SELECT l.* FROM input_0 AS l {how.upper()} JOIN input_1 AS r ON {on}"
    if how not in {"inner", "left", "right", "full"}:
        raise OpError(f"unknown join type '{how}'")

    suffix = cfg.get("suffix") or "_right"
    same_name_keys = {b for a, b in pairs if a == b}
    left_names = {n.lower() for n in names(left)}
    select: list[str] = []
    for n in names(left):
        if n in same_name_keys and how in {"right", "full"}:
            select.append(f"COALESCE(l.{ident(n)}, r.{ident(n)}) AS {ident(n)}")
        else:
            select.append(f"l.{ident(n)}")
    taken = set(left_names)
    for n in names(right):
        if n in same_name_keys:
            continue
        out = n
        while out.lower() in taken:
            out = f"{out}{suffix}"
        taken.add(out.lower())
        select.append(f"r.{ident(n)} AS {ident(out)}")
    join_kw = {"inner": "INNER", "left": "LEFT", "right": "RIGHT", "full": "FULL OUTER"}[how]
    return f"SELECT {', '.join(select)} FROM input_0 AS l {join_kw} JOIN input_1 AS r ON {on}"


register(OpSpec(
    id="join", label="Join", category="combine", inputs=2,
    description="Combine two inputs on matching key columns (connect left and right)",
    params=(
        Param("how", "Join type", "select", default="inner", options=_HOW),
        Param("keys", "Match on", "list", required=True, fields=(
            Param("left", "Left column", "column", required=True, input=0),
            Param("right", "Right column", "column", required=True, input=1),
        )),
        Param("suffix", "Suffix for clashing right columns", "text", default="_right"),
    ),
    compile=_join, preset_prompt="Join inputs[0] and inputs[1] on ",
))


def _union(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    distinct = bool(cfg.get("distinct"))
    kw = "UNION" if distinct else "UNION ALL"
    if cfg.get("by_name", True):
        return f"SELECT * FROM input_0 {kw} BY NAME SELECT * FROM input_1"
    if len(schemas[0]) != len(schemas[1]):
        raise OpError("matching by position needs the same number of columns on both inputs")
    return f"SELECT * FROM input_0 {kw} SELECT * FROM input_1"


register(OpSpec(
    id="union", label="Union / append", category="combine", inputs=2,
    description="Stack the rows of two inputs",
    params=(
        Param("by_name", "Match columns by name", "boolean", default=True,
              help="Off = match by position. Missing columns become empty."),
        Param("distinct", "Remove duplicate rows", "boolean", default=False),
    ),
    compile=_union, preset_prompt="Append inputs[1] below inputs[0]",
))
