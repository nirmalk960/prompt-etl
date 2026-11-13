"""Per-engine LLM prompt templates and a deterministic stub-codegen.

Each operation (`filter`, `select`, `aggregate`, ...) has three variants:
    pandas:   Python code that produces a pandas DataFrame named `result`
    polars:   Python code that produces a polars LazyFrame named `result`
    duckdb:   A single SQL SELECT (no `;`) that the engine wraps in COPY ... TO ...

The system prompt for each engine narrows the model to that engine's idioms so
the agent guard's per-engine validators rarely reject. The stub codegen path
is used when Ollama is disabled (tests, CI, or offline mode) and supports
simple, structured prompts like "Filter rows where department equals
Engineering" or "Keep columns id, name, salary".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..engines.base import EngineKind


@dataclass(slots=True)
class PromptBundle:
    system: str
    user: str
    stop: list[str]


# ---------------------------------------------------------------------------
# System prompts per engine
# ---------------------------------------------------------------------------

_PANDAS_SYSTEM = """You are a precise pandas code generator.
Rules:
- Output ONLY a Python code block (no prose, no markdown fences).
- Inputs are `inputs` (list of DataFrames) and `df` (= inputs[0]).
- Assign the final DataFrame to a variable named `result`.
- Do NOT import anything. `pd` and `pl` are pre-imported.
- Do NOT read/write files, network, env, subprocess, or os.
- Use ONLY pandas: .query, boolean indexing, .groupby, .merge, .drop_duplicates, .rename, .fillna, .dropna, .sort_values, column selection.
"""

_POLARS_SYSTEM = """You are a precise Polars code generator.
Rules:
- Output ONLY a Python code block (no prose, no markdown fences).
- Inputs are `inputs` (list of LazyFrames) and `df` (= inputs[0]).
- Assign the final pl.LazyFrame to a variable named `result`.
- Do NOT import anything. `pl` is pre-imported.
- Do NOT read/write files, network, env, subprocess, or os.
- Prefer lazy expressions: pl.col(...), .filter(...), .select(...), .with_columns(...), .group_by(...).agg(...), .sort(...), .unique(...), .drop_nulls(...), .fill_null(...).
"""

_DUCKDB_SYSTEM = """You are a precise DuckDB SQL generator.
Rules:
- Output ONLY a single SQL SELECT (or WITH ... SELECT) statement. No prose, no semicolons, no markdown.
- Inputs are exposed as views named input_0, input_1, ...; `df` aliases input_0.
- Forbidden: ATTACH, INSTALL, LOAD, COPY, PRAGMA, EXPORT, IMPORT, CREATE SECRET, SET, DDL.
- Use standard SQL: WHERE, GROUP BY, ORDER BY, JOIN, DISTINCT, CASE, COALESCE.
"""

_SYSTEM_BY_ENGINE: dict[EngineKind, str] = {
    "pandas": _PANDAS_SYSTEM,
    "polars": _POLARS_SYSTEM,
    "duckdb": _DUCKDB_SYSTEM,
    "spark": _PANDAS_SYSTEM,  # placeholder; Phase 2 will add Spark template
}


# ---------------------------------------------------------------------------
# Per-operation guidance (kept short so the model sees concrete idioms)
# ---------------------------------------------------------------------------

_OP_HINT_PANDAS: dict[str, str] = {
    "filter": "Use boolean indexing or .query() to keep rows matching the prompt.",
    "select": "Subset columns: result = df[[...]]",
    "drop_nulls": "result = df.dropna(subset=[...])  # subset omitted = all columns",
    "fill_nulls": "result = df.fillna({col: value, ...})",
    "rename": "result = df.rename(columns={old: new, ...})",
    "deduplicate": "result = df.drop_duplicates(subset=[...])",
    "aggregate": "result = df.groupby(['a']).agg(b_sum=('b', 'sum'), b_avg=('b', 'mean')).reset_index()  # named aggregation; fns: sum|mean|count|min|max",
    "sort": "result = df.sort_values(by=[...], ascending=[...])",
    "custom": "e.g. new column: result = df.assign(c=df['a'] - df['b']). Assign the final DataFrame to `result`.",
}

_OP_HINT_POLARS: dict[str, str] = {
    "filter": "result = df.filter(pl.col('x') == 'y')",
    "select": "result = df.select(['a', 'b'])",
    "drop_nulls": "result = df.drop_nulls(subset=['a'])",
    "fill_nulls": "result = df.with_columns(pl.col('a').fill_null(0))",
    "rename": "result = df.rename({'old': 'new'})",
    "deduplicate": "result = df.unique(subset=['a'])",
    "aggregate": "result = df.group_by(['a']).agg(pl.col('b').sum().alias('b_sum'))",
    "sort": "result = df.sort(['a'], descending=[False])",
    "custom": "e.g. new column: result = df.with_columns((pl.col('a') - pl.col('b')).alias('c')). Only reference columns from the input schema. Assign the final LazyFrame to `result`.",
}

_OP_HINT_DUCKDB: dict[str, str] = {
    "filter": "SELECT * FROM df WHERE <predicate>",
    "select": "SELECT col1, col2 FROM df",
    "drop_nulls": "SELECT * FROM df WHERE col IS NOT NULL",
    "fill_nulls": "SELECT * REPLACE (COALESCE(col, 0) AS col) FROM df  -- keep the original column name",
    "rename": "SELECT col AS new_name FROM df",
    "deduplicate": "SELECT DISTINCT * FROM df  -- or SELECT DISTINCT ON (cols) ...",
    "aggregate": "SELECT a, SUM(b) AS b_sum FROM df GROUP BY a",
    "sort": "SELECT * FROM df ORDER BY col [ASC|DESC]",
    "custom": "e.g. new column: SELECT *, a - b AS c FROM df",
}

_OP_HINT_BY_ENGINE: dict[EngineKind, dict[str, str]] = {
    "pandas": _OP_HINT_PANDAS,
    "polars": _OP_HINT_POLARS,
    "duckdb": _OP_HINT_DUCKDB,
    "spark": _OP_HINT_PANDAS,
}


def build_prompt(
    *,
    engine: EngineKind,
    node_kind: str,
    user_prompt: str,
    schema: list[dict[str, str]],
) -> PromptBundle:
    system = _SYSTEM_BY_ENGINE[engine]
    hint = _OP_HINT_BY_ENGINE[engine].get(node_kind, "")
    cols = ", ".join(f"{c['name']} ({c['dtype']})" for c in schema[:30])
    user = (
        f"Operation: {node_kind}\n"
        f"Input schema: {cols}\n"
        f"Idiom hint: {hint}\n\n"
        f"User request: {user_prompt}\n\n"
        "Return only the code/SQL, no prose."
    )
    # No "```" stop sequence: models open with a fence, so it would end generation
    # before any code is emitted. extract_code() strips fences instead.
    return PromptBundle(system=system, user=user, stop=[])


# ---------------------------------------------------------------------------
# Deterministic stub codegen
# Recognises a few common phrasings so the system is usable without Ollama.
# ---------------------------------------------------------------------------

# Longer phrases first so "greater than or equal to" wins over "greater than",
# and "is greater than" is never read as equality with the word "greater".
_FILTER_OPS: list[tuple[str, str]] = [
    (">=", r">=|\bgreater\s+than\s+or\s+equal(?:\s+to)?\b|\bat\s+least\b|\bno\s+less\s+than\b|\bgte\b"),
    ("<=", r"<=|\bless\s+than\s+or\s+equal(?:\s+to)?\b|\bat\s+most\b|\bno\s+more\s+than\b|\blte\b"),
    ("!=", r"!=|<>|\bnot\s+equal(?:s|\s+to)?\b|\bdoes\s+not\s+equal\b|\bnot\b"),
    (">", r">|\bgreater\s+than\b|\bmore\s+than\b|\bhigher\s+than\b|\babove\b|\bover\b|\bgt\b"),
    ("<", r"<|\bless\s+than\b|\blower\s+than\b|\bbelow\b|\bunder\b|\blt\b"),
    ("==", r"==|=|\bequals?(?:\s+to)?\b|\bequal\s+to\b|\bis\b"),
]
_NUMBER = r"-?\d+(?:\.\d+)?"
_FILTER_HEAD = r"(?:filter|keep)\s+(?:only\s+)?(?:the\s+)?rows?\s+where\s+([A-Za-z_]\w*)\s*(?:\bis\s+)?"
_FILTER_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        op,
        re.compile(
            _FILTER_HEAD
            + rf"(?:{words})\s*"
            + (
                rf"({_NUMBER})"
                if op in {">", "<", ">=", "<="}
                else r"(?=\S)(?!(?:in|like|null|none|empty|missing|between|contains?|greater|less|more|"
                r"higher|lower|above|below|over|under|at|equal|not)\b)['\"]?(.+?)['\"]?"
            )
            + r"\s*\.?\s*$",
            re.IGNORECASE,
        ),
    )
    for op, words in _FILTER_OPS
]
_SELECT_COLS = re.compile(
    r"(?:keep|select)\s+columns?\s+([\w,\s]+?)(?:\.|$)",
    re.IGNORECASE,
)
_RENAME = re.compile(
    r"rename\s+column\s+(\w+)\s+to\s+(\w+)",
    re.IGNORECASE,
)
_DROP_NULLS = re.compile(
    r"(?:drop|remove)\s+(?:rows\s+with\s+)?(?:null|na|missing)(?:\s+values?)?(?:\s+in\s+(\w+))?",
    re.IGNORECASE,
)
_DEDUP = re.compile(
    r"(?:deduplicate|remove\s+duplicates?)(?:\s+(?:on|by)\s+([\w,\s]+))?",
    re.IGNORECASE,
)
_AGG = re.compile(
    r"(sum|mean|avg|count|min|max)\s+of\s+(\w+)(?:\s+(?:by|grouped\s+by)\s+([\w,\s]+))?",
    re.IGNORECASE,
)
_GROUP_BY_CALC = re.compile(
    r"group\s+by\s+(.+?)(?:\s+and\s+calculate\s+|\s+calculate\s+|$)",
    re.IGNORECASE,
)
_AGG_METRIC = re.compile(
    r"([A-Za-z_][\w\s]*)\s*\(\s*(sum|mean|avg|count|min|max)\s+([A-Za-z_]\w*)\s*\)",
    re.IGNORECASE,
)
_AGG_METRIC_AS = re.compile(
    r"(sum|mean|avg|count|min|max)\s*\(\s*([A-Za-z_]\w*)\s*\)\s+as\s+([A-Za-z_]\w*)",
    re.IGNORECASE,
)
_AGG_METRIC_EQ = re.compile(
    r"([A-Za-z_][\w\s]*)\s*(?:=|:)\s*(sum|mean|avg|count|min|max)\s*(?:of\s+)?([A-Za-z_]\w*)",
    re.IGNORECASE,
)
_MONTH_GROUP = re.compile(r"month\s*\(\s*([A-Za-z_]\w*)\s*\)", re.IGNORECASE)
_SORT = re.compile(
    r"sort\s+by\s+(\w+)(?:\s+(asc|ascending|desc|descending))?",
    re.IGNORECASE,
)
_GROUP_INTENT = re.compile(r"\b(group\s+by|grouped\s+by|month\s*\()", re.IGNORECASE)


def _cols_list(s: str) -> list[str]:
    return [c.strip() for c in s.replace(" and ", ",").split(",") if c.strip()]


def _clean_metric_alias(alias_raw: str, *, source_col: str, fn_raw: str) -> str:
    """Normalize user-provided metric aliases.

    - Removes common prompt filler prefixes like "and calculate".
    - Keeps meaningful wording from the user's alias.
    - Falls back to the original source column name when alias is unclear.
    """
    alias = alias_raw.strip().lower()
    # Alias patterns capture greedily, e.g. "and region and calculate total_revenue"
    # before "(sum ...)"; only the last clause names the metric.
    alias = re.split(r"\b(?:and|then|calculate|compute)\b|,", alias)[-1]
    alias = re.sub(r"^[^a-z0-9_]+", "", alias)
    alias = re.sub(
        r"^(?:and|then|calculate|compute|get|show|with|the|a|an|metric|metrics)\b[\s_]*",
        "",
        alias,
    )
    alias = re.sub(
        r"^(?:and|then|calculate|compute|get|show|with|the|a|an|metric|metrics)\b[\s_]*",
        "",
        alias,
    )
    alias = re.sub(r"[^a-z0-9_ ]+", " ", alias).strip().replace(" ", "_")
    if not alias:
        # Preserve original metric column identity if alias text is unusable.
        return source_col
    if alias in {"sum", "avg", "mean", "count", "min", "max"}:
        return source_col
    if alias.startswith(("and_", "calculate_", "compute_", "then_")):
        alias = alias.split("_", 1)[1] if "_" in alias else alias
    if not alias:
        return source_col
    return alias


def stub_codegen(
    *,
    engine: EngineKind,
    node_kind: str,
    user_prompt: str,
    schema: list[dict[str, str]] | None = None,
) -> str | None:
    """Return generated code for a small set of recognised prompts.

    Used when Ollama is unavailable so the system is testable end-to-end.
    Returns ``None`` if no rule matches (caller should fall back to Ollama or
    surface a clear error). With ``schema``, filters on unknown columns or that
    compare a numeric column to text also return ``None``.
    """
    p = user_prompt.strip()
    dtypes = {c["name"]: c["dtype"] for c in schema} if schema else None

    if node_kind == "filter":
        for op, rule in _FILTER_RULES:
            if (m := rule.search(p)):
                col, val = m.group(1), m.group(2).strip()
                dtype = (dtypes or {}).get(col)
                if dtypes is not None and dtype is None:
                    return None
                if dtype and _is_numeric_dtype(dtype) and not re.fullmatch(_NUMBER, val):
                    # Comparing a number column to text silently matches nothing;
                    # let the LLM interpret the prompt instead.
                    return None
                return _filter(engine, col, op, val)
        return None

    if node_kind == "select":
        if (m := _SELECT_COLS.search(p)):
            cols = _cols_list(m.group(1))
            return _select(engine, cols)

    if node_kind == "rename":
        if (m := _RENAME.search(p)):
            return _rename(engine, m.group(1), m.group(2))

    if node_kind == "drop_nulls":
        m = _DROP_NULLS.search(p)
        if m:
            return _drop_nulls(engine, m.group(1) if m.lastindex else None)

    if node_kind == "deduplicate":
        m = _DEDUP.search(p)
        if m:
            cols = _cols_list(m.group(1)) if m.group(1) else None
            return _dedup(engine, cols)

    if node_kind == "aggregate":
        gb = _agg_from_groupby_prompt(engine, p)
        if gb is not None:
            return gb
        # If user clearly asked for grouped aggregation but we couldn't parse it
        # reliably, do NOT fall back to simplistic global aggregate templates.
        # Returning None lets Ollama handle the richer prompt instead.
        if _GROUP_INTENT.search(p):
            return None
        if (m := _AGG.search(p)):
            return _agg(engine, m.group(1).lower(), m.group(2), _cols_list(m.group(3) or ""))

    if node_kind == "sort":
        if (m := _SORT.search(p)):
            desc = (m.group(2) or "").lower().startswith("desc")
            return _sort(engine, m.group(1), desc)

    return None


def _is_numeric_dtype(dtype: str) -> bool:
    return bool(re.match(r"(?:u?int(?!erval)|float|double|decimal|numeric|real|bigint|smallint|tinyint|hugeint)", dtype.strip().lower()))


# Codegen per (op, engine).
def _quote_val(v: str) -> str:
    if re.match(r"^-?\d+(\.\d+)?$", v):
        return v
    return repr(v)


def _filter(engine: EngineKind, col: str, op: str, val: str) -> str:
    vlit = _quote_val(val)
    if engine == "pandas":
        return f"result = df[df[{col!r}] {op} {vlit}]"
    if engine == "polars":
        return f"result = df.filter(pl.col({col!r}) {op} {vlit})"
    if engine == "duckdb":
        sql_op = op
        return f"SELECT * FROM df WHERE {col} {sql_op} {vlit}"
    raise ValueError(engine)


def _select(engine: EngineKind, cols: list[str]) -> str:
    if engine == "pandas":
        return f"result = df[{cols!r}]"
    if engine == "polars":
        return f"result = df.select({cols!r})"
    if engine == "duckdb":
        return "SELECT " + ", ".join(cols) + " FROM df"
    raise ValueError(engine)


def _rename(engine: EngineKind, old: str, new: str) -> str:
    if engine == "pandas":
        return f"result = df.rename(columns={{{old!r}: {new!r}}})"
    if engine == "polars":
        return f"result = df.rename({{{old!r}: {new!r}}})"
    if engine == "duckdb":
        return f"SELECT * RENAME ({old} AS {new}) FROM df"
    raise ValueError(engine)


def _drop_nulls(engine: EngineKind, col: str | None) -> str:
    if engine == "pandas":
        if col:
            return f"result = df.dropna(subset=[{col!r}])"
        return "result = df.dropna()"
    if engine == "polars":
        if col:
            return f"result = df.drop_nulls(subset=[{col!r}])"
        return "result = df.drop_nulls()"
    if engine == "duckdb":
        if col:
            return f"SELECT * FROM df WHERE {col} IS NOT NULL"
        return "SELECT * FROM df WHERE COLUMNS(*) IS NOT NULL"
    raise ValueError(engine)


def _dedup(engine: EngineKind, cols: list[str] | None) -> str:
    if engine == "pandas":
        if cols:
            return f"result = df.drop_duplicates(subset={cols!r})"
        return "result = df.drop_duplicates()"
    if engine == "polars":
        if cols:
            return f"result = df.unique(subset={cols!r})"
        return "result = df.unique()"
    if engine == "duckdb":
        if cols:
            return "SELECT DISTINCT ON (" + ", ".join(cols) + ") * FROM df"
        return "SELECT DISTINCT * FROM df"
    raise ValueError(engine)


def _agg(engine: EngineKind, fn: str, col: str, by: list[str]) -> str:
    fn_map = {"avg": "mean"}
    pdfn = fn_map.get(fn, fn)
    if engine == "pandas":
        if by:
            return (
                f"result = df.groupby({by!r}).agg({{ {col!r}: {pdfn!r} }})"
                ".reset_index()"
            )
        return f"result = df[[{col!r}]].agg({pdfn!r}).to_frame().T"
    if engine == "polars":
        if by:
            return (
                f"result = df.group_by({by!r}).agg("
                f"pl.col({col!r}).{pdfn}().alias('{col}_{pdfn}'))"
            )
        return f"result = df.select(pl.col({col!r}).{pdfn}().alias('{col}_{pdfn}'))"
    if engine == "duckdb":
        sqlfn = "AVG" if fn in {"mean", "avg"} else fn.upper()
        if by:
            return (
                "SELECT "
                + ", ".join(by)
                + f", {sqlfn}({col}) AS {col}_{fn} FROM df GROUP BY "
                + ", ".join(by)
            )
        return f"SELECT {sqlfn}({col}) AS {col}_{fn} FROM df"
    raise ValueError(engine)


def _agg_from_groupby_prompt(engine: EngineKind, prompt: str) -> str | None:
    """Parse richer aggregate phrasing:
    'Group by region and month(order_date) and calculate total revenue (sum order_total), ...'
    """
    m = _GROUP_BY_CALC.search(prompt)
    if not m:
        return None

    group_part = m.group(1)
    metrics: list[tuple[str, str, str]] = list(_AGG_METRIC.findall(prompt))
    for fn_raw, col, alias_raw in _AGG_METRIC_AS.findall(prompt):
        metrics.append((alias_raw, fn_raw, col))
    for alias_raw, fn_raw, col in _AGG_METRIC_EQ.findall(prompt):
        metrics.append((alias_raw, fn_raw, col))

    # De-duplicate while preserving order.
    deduped: list[tuple[str, str, str]] = []
    seen_metrics: set[tuple[str, str, str]] = set()
    for alias_raw, fn_raw, col in metrics:
        key = (
            alias_raw.strip().lower().replace(" ", "_"),
            fn_raw.strip().lower(),
            col.strip(),
        )
        if key in seen_metrics:
            continue
        seen_metrics.add(key)
        deduped.append((alias_raw, fn_raw, col))
    metrics = deduped
    if not metrics:
        return None

    by_cols: list[str] = []
    month_source: str | None = None
    for tok in _cols_list(group_part):
        tok = tok.strip()
        mm = _MONTH_GROUP.search(tok)
        if mm:
            month_source = mm.group(1)
            by_cols.append("month")
        elif tok:
            by_cols.append(tok)
    # Deduplicate while preserving order
    seen = set()
    by_cols = [c for c in by_cols if not (c in seen or seen.add(c))]

    if engine == "pandas":
        lines: list[str] = []
        if month_source:
            lines.append("tmp = df.copy()")
            lines.append(
                f"tmp['month'] = pd.to_datetime(tmp[{month_source!r}], errors='coerce').dt.to_period('M').astype(str)"
            )
        else:
            lines.append("tmp = df")

        agg_entries: list[str] = []
        for alias_raw, fn_raw, col in metrics:
            fn = fn_raw.lower().replace("avg", "mean")
            alias = _clean_metric_alias(alias_raw, source_col=col, fn_raw=fn_raw)
            agg_entries.append(f"{alias}=({col!r}, {fn!r})")

        if by_cols:
            lines.append(
                f"result = tmp.groupby({by_cols!r}).agg({', '.join(agg_entries)}).reset_index()"
            )
        else:
            scalar_parts: list[str] = []
            for alias_raw, fn_raw, col in metrics:
                fn = fn_raw.lower().replace("avg", "mean")
                alias = _clean_metric_alias(alias_raw, source_col=col, fn_raw=fn_raw)
                scalar_parts.append(f"{alias!r}: tmp[{col!r}].agg({fn!r})")
            lines.append(f"result = pd.DataFrame([{{{', '.join(scalar_parts)}}}])")
        return "\n".join(lines)

    if engine == "polars":
        group_expr: list[str] = []
        pre_cols: list[str] = []
        if month_source:
            # Cast first: the source column may be a real Date/Datetime dtype
            # (Parquet ingest infers dates), where .str accessors fail.
            pre_cols.append(
                f"pl.col({month_source!r}).cast(pl.Utf8).str.slice(0, 7).alias('month')"
            )
            group_expr.append("'month'")
        for c in by_cols:
            if c != "month":
                group_expr.append(repr(c))

        agg_exprs: list[str] = []
        for alias_raw, fn_raw, col in metrics:
            fn = fn_raw.lower().replace("avg", "mean")
            alias = _clean_metric_alias(alias_raw, source_col=col, fn_raw=fn_raw)
            agg_exprs.append(f"pl.col({col!r}).{fn}().alias({alias!r})")

        lines = []
        if pre_cols:
            lines.append(f"tmp = df.with_columns({', '.join(pre_cols)})")
        else:
            lines.append("tmp = df")
        if group_expr:
            lines.append(f"result = tmp.group_by([{', '.join(group_expr)}]).agg({', '.join(agg_exprs)})")
        else:
            lines.append(f"result = tmp.select({', '.join(agg_exprs)})")
        return "\n".join(lines)

    if engine == "duckdb":
        select_parts: list[str] = []
        group_parts: list[str] = []
        if month_source:
            select_parts.append(f"SUBSTR(CAST({month_source} AS VARCHAR), 1, 7) AS month")
            group_parts.append("month")
        for c in by_cols:
            if c != "month":
                select_parts.append(c)
                group_parts.append(c)
        for alias_raw, fn_raw, col in metrics:
            fn = "AVG" if fn_raw.lower() in {"avg", "mean"} else fn_raw.upper()
            alias = _clean_metric_alias(alias_raw, source_col=col, fn_raw=fn_raw)
            select_parts.append(f"{fn}({col}) AS {alias}")
        if group_parts:
            return f"SELECT {', '.join(select_parts)} FROM df GROUP BY {', '.join(group_parts)}"
        return f"SELECT {', '.join(select_parts)} FROM df"

    return None


def _sort(engine: EngineKind, col: str, desc: bool) -> str:
    if engine == "pandas":
        return f"result = df.sort_values(by={col!r}, ascending={not desc})"
    if engine == "polars":
        return f"result = df.sort({col!r}, descending={desc})"
    if engine == "duckdb":
        return f"SELECT * FROM df ORDER BY {col} {'DESC' if desc else 'ASC'}"
    raise ValueError(engine)


# ---------------------------------------------------------------------------
# Passthrough / non-LLM codegen (for source and write nodes; default for "custom"
# when the stub can't recognise the prompt and Ollama is unavailable).
# ---------------------------------------------------------------------------

def passthrough_code(engine: EngineKind) -> str:
    if engine == "pandas":
        return "result = df"
    if engine == "polars":
        return "result = df"
    if engine == "duckdb":
        return "SELECT * FROM df"
    raise ValueError(engine)


# Compatibility export for routers/tests
EngineLiteral = Literal["pandas", "polars", "duckdb"]
