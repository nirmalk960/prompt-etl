"""Quoting, type helpers and validation for compiling ops to DuckDB SQL.

Every identifier and literal that comes from user settings goes through
``ident``/``lit``/``typed_lit``. Free-form expressions (formula columns) go
through ``safe_expr``; the runner additionally executes all op SQL on a
connection whose file access is limited to the step's temp directories.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from .base import OpError, Schema


def ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def lit(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise OpError(f"invalid number: {value}")
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


_NUMBER_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s*$")


def is_number_text(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) or (
        isinstance(value, str) and bool(_NUMBER_RE.match(value))
    )


def to_number(value: Any, what: str) -> int | float:
    if isinstance(value, bool):
        raise OpError(f"{what}: expected a number, got {value!r}")
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and _NUMBER_RE.match(value):
        f = float(value)
        return int(f) if f.is_integer() and "." not in value and "e" not in value.lower() else f
    raise OpError(f"{what}: expected a number, got {value!r}")


def to_int(value: Any, what: str, *, minimum: int | None = None) -> int:
    n = to_number(value, what)
    if isinstance(n, float):
        if not n.is_integer():
            raise OpError(f"{what}: expected a whole number, got {value!r}")
        n = int(n)
    if minimum is not None and n < minimum:
        raise OpError(f"{what}: must be at least {minimum}")
    return n


_NUMERIC = re.compile(
    r"^(TINYINT|SMALLINT|INTEGER|INT|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|UHUGEINT|"
    r"FLOAT|REAL|DOUBLE|DECIMAL|NUMERIC)\b(?!\s*\[)",
    re.IGNORECASE,
)
_TEMPORAL = re.compile(r"^(DATE|TIMESTAMP|TIME|DATETIME)\b(?!\s*\[)", re.IGNORECASE)
_BOOL = re.compile(r"^(BOOLEAN|BOOL)$", re.IGNORECASE)


def is_numeric(dtype: str) -> bool:
    return bool(_NUMERIC.match(dtype.strip()))


def is_temporal(dtype: str) -> bool:
    return bool(_TEMPORAL.match(dtype.strip()))


def is_bool(dtype: str) -> bool:
    return bool(_BOOL.match(dtype.strip()))


def is_text(dtype: str) -> bool:
    return dtype.strip().upper().startswith("VARCHAR")


def names(schema: Schema) -> list[str]:
    return [n for n, _ in schema]


def resolve(col: Any, schema: Schema, what: str = "column") -> str:
    """Return the schema's spelling of ``col``; raise if it doesn't exist."""
    if not isinstance(col, str) or not col.strip():
        raise OpError(f"{what} is required")
    available = names(schema)
    if col in available:
        return col
    lowered = {n.lower(): n for n in available}
    if col.strip().lower() in lowered:
        return lowered[col.strip().lower()]
    raise OpError(f"unknown {what} '{col}'. Available: {', '.join(available[:25])}")


def resolve_all(cols: Iterable[Any] | None, schema: Schema, what: str = "column") -> list[str]:
    return [resolve(c, schema, what) for c in (cols or [])]


def dtype_of(col: str, schema: Schema) -> str:
    for n, t in schema:
        if n == col:
            return t
    raise OpError(f"unknown column '{col}'")


def typed_lit(value: Any, dtype: str, col: str) -> str:
    """Literal for comparing against a column; rejects text vs number mismatches."""
    if value is None or (isinstance(value, str) and value == "" and not is_text(dtype)):
        raise OpError(f"a value is required to compare with '{col}'")
    if is_numeric(dtype):
        if not is_number_text(value):
            raise OpError(f"'{col}' is numeric but the value {value!r} is not a number")
        return lit(to_number(value, col))
    if is_bool(dtype):
        s = str(value).strip().lower()
        if s in {"true", "yes", "1", "y", "t"}:
            return "TRUE"
        if s in {"false", "no", "0", "n", "f"}:
            return "FALSE"
        raise OpError(f"'{col}' is true/false but the value {value!r} is not")
    if is_temporal(dtype):
        base = dtype.split("(")[0].strip().upper()
        cast = "DATE" if base == "DATE" else "TIMESTAMP"
        return f"CAST({lit(str(value))} AS {cast})"
    return lit(str(value))


def auto_lit(value: Any) -> str:
    """Literal for an output value typed by the user (number if it looks like one)."""
    if value is None or value == "":
        return "NULL"
    if is_number_text(value):
        return lit(to_number(value, "value"))
    return lit(str(value))


def split_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def new_name(value: Any, what: str = "new column name") -> str:
    if not isinstance(value, str) or not value.strip():
        raise OpError(f"{what} is required")
    return value.strip()


# ---------------------------------------------------------------------------
# Free-form expressions
# ---------------------------------------------------------------------------

_STRING_LIT = re.compile(r"'(?:[^']|'')*'")
_QUOTED_IDENT = re.compile(r'"((?:[^"]|"")*)"')
_FORBIDDEN_WORDS = re.compile(
    r"\b(select|union|intersect|except|copy|attach|detach|install|load|pragma|create|drop|"
    r"alter|insert|update|delete|export|import|call|checkpoint|vacuum|set|reset)\b",
    re.IGNORECASE,
)
_FORBIDDEN_FUNCS = re.compile(
    r"\b(read_\w+|glob|getenv|query|query_table|sniff_csv|parquet_\w+|iceberg_\w+|delta_\w+|"
    r"current_setting|duckdb_\w+|pragma_\w+|which_secret|load_aws_credentials|system)\s*\(",
    re.IGNORECASE,
)


def safe_expr(expr: Any, schema: Schema) -> str:
    if not isinstance(expr, str) or not expr.strip():
        raise OpError("expression is required")
    expr = expr.strip()
    if len(expr) > 2000:
        raise OpError("expression is too long (max 2000 characters)")
    if ";" in _STRING_LIT.sub("''", expr) or "--" in expr or "/*" in expr:
        raise OpError("expression may not contain ';' or comments")
    without_strings = _STRING_LIT.sub("''", expr)
    for m in _QUOTED_IDENT.finditer(without_strings):
        resolve(m.group(1).replace('""', '"'), schema)
    bare = _QUOTED_IDENT.sub('""', without_strings)
    if (m := _FORBIDDEN_WORDS.search(bare)) is not None:
        raise OpError(f"expression may not use '{m.group(1)}'; use a single-row formula")
    if (m := _FORBIDDEN_FUNCS.search(bare)) is not None:
        raise OpError(f"function '{m.group(1)}' is not allowed in expressions")
    return expr


# ---------------------------------------------------------------------------
# Type names from other systems -> DuckDB (for preflight dry runs)
# ---------------------------------------------------------------------------

_TYPE_MAP = [
    (re.compile(r"^(u?int(8|16|32|64)|int|integer|bigint|smallint|tinyint|hugeint|long)$", re.I), "BIGINT"),
    (re.compile(r"^(float(16|32|64)?|double|real|halffloat|decimal.*|numeric.*)$", re.I), "DOUBLE"),
    (re.compile(r"^(bool|boolean)$", re.I), "BOOLEAN"),
    (re.compile(r"^(date|date32.*|date64.*)$", re.I), "DATE"),
    (re.compile(r"^(datetime.*|timestamp.*)$", re.I), "TIMESTAMP"),
    (re.compile(r"^(time.*)$", re.I), "TIME"),
]


def to_duckdb_type(dtype: str) -> str:
    t = (dtype or "").strip()
    if not t:
        return "VARCHAR"
    base = re.sub(r"\[.*\]$", "", t)
    for rx, mapped in _TYPE_MAP:
        if rx.match(base):
            return mapped
    if is_numeric(t) or is_temporal(t) or is_bool(t):
        return t.upper()
    return "VARCHAR"
