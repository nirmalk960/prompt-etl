"""Text operations."""

from __future__ import annotations

from typing import Any

from .base import OpError, OpSpec, Param, Schema, opts
from .registry import register
from .sqlsafe import ident, lit, names, new_name, resolve, resolve_all, to_int


def _clean_text(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    cols = resolve_all(cfg.get("columns"), schemas[0])
    if not cols:
        raise OpError("choose at least one column")
    parts = []
    for col in cols:
        e = f"CAST({ident(col)} AS VARCHAR)"
        if cfg.get("trim", True):
            e = f"trim({e})"
        if cfg.get("collapse_spaces", True):
            e = f"regexp_replace({e}, '\\s+', ' ', 'g')"
        case = cfg.get("case") or "none"
        if case == "upper":
            e = f"upper({e})"
        elif case == "lower":
            e = f"lower({e})"
        elif case == "title":
            e = (f"array_to_string(list_transform(string_split(lower({e}), ' '), "
                 f"lambda w: upper(w[1]) || w[2:]), ' ')")
        if cfg.get("empty_as_null", True):
            e = f"nullif({e}, '')"
        parts.append(f"{e} AS {ident(col)}")
    return f"SELECT * REPLACE ({', '.join(parts)}) FROM df"


register(OpSpec(
    id="clean_text", label="Clean text", category="text",
    description="Trim spaces, fix case and blank out empty strings",
    params=(
        Param("columns", "Columns", "columns", required=True),
        Param("trim", "Trim leading/trailing spaces", "boolean", default=True),
        Param("collapse_spaces", "Collapse repeated spaces", "boolean", default=True),
        Param("case", "Change case", "select", default="none",
              options=opts(("none", "keep as is"), "upper", "lower", ("title", "Title Case"))),
        Param("empty_as_null", "Treat empty text as missing", "boolean", default=True),
    ),
    compile=_clean_text,
))


def _replace(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    col = resolve(cfg.get("column"), schemas[0])
    find = cfg.get("find")
    if find is None or find == "":
        raise OpError("text to find is required")
    rep = "" if cfg.get("replace_with") is None else str(cfg.get("replace_with"))
    src = f"CAST({ident(col)} AS VARCHAR)"
    if cfg.get("mode") == "regex":
        flags = "g" if cfg.get("case_sensitive", True) else "gi"
        e = f"regexp_replace({src}, {lit(str(find))}, {lit(rep)}, {lit(flags)})"
    elif cfg.get("case_sensitive", True):
        e = f"replace({src}, {lit(str(find))}, {lit(rep)})"
    else:
        pattern = "".join("\\" + ch if not ch.isalnum() else ch for ch in str(find))
        e = f"regexp_replace({src}, {lit(pattern)}, {lit(rep)}, 'gi')"
    return f"SELECT * REPLACE ({e} AS {ident(col)}) FROM df"


register(OpSpec(
    id="replace_text", label="Find and replace", category="text",
    description="Replace text or a regex pattern inside a column",
    params=(
        Param("column", "Column", "column", required=True),
        Param("find", "Find", "text", required=True),
        Param("replace_with", "Replace with", "text"),
        Param("mode", "Match", "select", default="text", options=opts(("text", "exact text"), "regex")),
        Param("case_sensitive", "Case sensitive", "boolean", default=True),
    ),
    compile=_replace,
))


def _split(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    col = resolve(cfg.get("column"), schemas[0])
    delim = cfg.get("delimiter")
    if delim is None or delim == "":
        raise OpError("delimiter is required")
    parts = to_int(cfg.get("parts", 2), "number of parts", minimum=1)
    if parts > 50:
        raise OpError("at most 50 parts")
    prefix = (cfg.get("prefix") or col).strip()
    existing = {n.lower() for n in names(schemas[0])}
    new_cols = []
    for i in range(1, parts + 1):
        name = f"{prefix}_{i}"
        if name.lower() in existing:
            raise OpError(f"column '{name}' already exists; choose another prefix")
        piece = f"nullif(split_part(CAST({ident(col)} AS VARCHAR), {lit(str(delim))}, {i}), '')"
        new_cols.append(f"{piece} AS {ident(name)}")
    base = "*" if cfg.get("keep_original", True) else f"* EXCLUDE ({ident(col)})"
    return f"SELECT {base}, {', '.join(new_cols)} FROM df"


register(OpSpec(
    id="split_column", label="Split column", category="text",
    description="Split text by a delimiter into numbered columns",
    params=(
        Param("column", "Column", "column", required=True),
        Param("delimiter", "Delimiter", "text", required=True, default=","),
        Param("parts", "Number of parts", "integer", default=2),
        Param("prefix", "New column prefix", "text", help="Defaults to the column name"),
        Param("keep_original", "Keep original column", "boolean", default=True),
    ),
    compile=_split,
))


def _extract(cfg: dict[str, Any], schemas: list[Schema]) -> str:
    col = resolve(cfg.get("column"), schemas[0])
    pattern = cfg.get("pattern")
    if not pattern:
        raise OpError("pattern is required")
    group = to_int(cfg.get("group") or 0, "group", minimum=0)
    name = new_name(cfg.get("new_column"))
    if name.lower() in {n.lower() for n in names(schemas[0])}:
        raise OpError(f"column '{name}' already exists")
    e = f"nullif(regexp_extract(CAST({ident(col)} AS VARCHAR), {lit(str(pattern))}, {group}), '')"
    return f"SELECT *, {e} AS {ident(name)} FROM df"


register(OpSpec(
    id="regex_extract", label="Extract pattern", category="text",
    description="Pull a regex match (or capture group) into a new column",
    params=(
        Param("column", "Column", "column", required=True),
        Param("pattern", "Regex pattern", "text", required=True, placeholder="([A-Z]{2})-\\d+"),
        Param("group", "Capture group", "integer", default=0, help="0 = whole match"),
        Param("new_column", "New column", "text", required=True),
    ),
    compile=_extract,
))
