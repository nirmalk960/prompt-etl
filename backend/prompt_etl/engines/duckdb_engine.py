"""DuckDB engine.

LLM emits SQL. Convention:
    - Inputs are registered as views named input_0, input_1, ...
    - Single-input convenience: view ``df`` aliases input_0.
    - The SQL must be a single ``SELECT`` statement that materializes the result.
The engine wraps it as ``COPY (<sql>) TO '<out>' (FORMAT PARQUET, COMPRESSION ZSTD)``
so even datasets larger than RAM stream through DuckDB's spill-to-disk executor.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import duckdb

from .base import DataEngine, StepPreview, StepResult
from ._io import staged_inputs, staged_output, upload_output

_FORBIDDEN_SQL = re.compile(
    r"\b(ATTACH|INSTALL|LOAD|COPY|PRAGMA|EXPORT|IMPORT|CREATE\s+SECRET|SET\s+)\b",
    re.IGNORECASE,
)


def _validate_sql(sql: str) -> str:
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise RuntimeError("duckdb step produced empty SQL")
    if not re.match(r"^\s*(WITH|SELECT)\b", sql, re.IGNORECASE):
        raise RuntimeError("duckdb step SQL must start with SELECT or WITH")
    if _FORBIDDEN_SQL.search(sql):
        raise RuntimeError("duckdb step SQL contains a forbidden keyword")
    return sql


def _sql_literal(path: str) -> str:
    """Quote a local file path safely as a DuckDB string literal."""
    return "'" + path.replace("'", "''") + "'"


def _preview_from_pandas(df, *, rows: int, bytes_: int | None) -> StepPreview:
    """Convert a pandas DataFrame head to a StepPreview, NaN-safe."""
    import pandas as pd  # local import: only needed for previews

    safe = df.astype(object).where(df.notnull(), None) if len(df) else df
    return StepPreview(
        columns=[str(c) for c in df.columns],
        rows=safe.values.tolist() if len(df) else [],
        row_count=rows,
        bytes=bytes_,
        schema=[{"name": str(c), "dtype": str(df[c].dtype)} for c in df.columns],
    )


def _run_sync(sql: str, input_paths: list[str], output_path: str) -> tuple[int, int]:
    sql = _validate_sql(sql)
    con = duckdb.connect()
    try:
        con.execute("PRAGMA threads=4")
        # DuckDB does NOT allow parameterized DDL. Paths are local temp files
        # we just downloaded — safe to interpolate after escaping single quotes.
        for i, p in enumerate(input_paths):
            con.execute(
                f"CREATE OR REPLACE VIEW input_{i} AS SELECT * FROM read_parquet({_sql_literal(p)})"
            )
        if input_paths:
            con.execute("CREATE OR REPLACE VIEW df AS SELECT * FROM input_0")
        copy_stmt = (
            f"COPY ({sql}) TO {_sql_literal(output_path)} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        con.execute(copy_stmt)
        rows = con.execute(
            f"SELECT COUNT(*) FROM read_parquet({_sql_literal(output_path)})"
        ).fetchone()[0]
        return int(rows), 0
    finally:
        con.close()


class DuckDBEngine(DataEngine):
    kind = "duckdb"

    async def execute_step(
        self,
        *,
        node_id: str,
        node_kind: str,
        code: str,
        input_object_keys: list[str],
        output_object_key: str,
        config: dict[str, Any] | None = None,
    ) -> StepResult:
        loop = asyncio.get_running_loop()

        def _work() -> StepResult:
            with staged_inputs(input_object_keys) as in_paths, staged_output() as out_path:
                _run_sync(code, in_paths, out_path)
                size, rows = upload_output(out_path, output_object_key)
                con = duckdb.connect()
                try:
                    head = con.execute(
                        f"SELECT * FROM read_parquet({_sql_literal(out_path)}) LIMIT 50"
                    ).df()
                finally:
                    con.close()
                preview = _preview_from_pandas(head, rows=rows, bytes_=size)
                return StepResult(
                    output_object_key=output_object_key,
                    row_count=rows,
                    byte_size=size,
                    preview=preview,
                )

        return await loop.run_in_executor(None, _work)

    async def preview(self, *, object_key: str, n: int = 50) -> StepPreview:
        loop = asyncio.get_running_loop()

        def _work() -> StepPreview:
            with staged_inputs([object_key]) as paths:
                con = duckdb.connect()
                try:
                    head = con.execute(
                        f"SELECT * FROM read_parquet({_sql_literal(paths[0])}) LIMIT {int(n)}"
                    ).df()
                finally:
                    con.close()
                return _preview_from_pandas(head, rows=len(head), bytes_=None)

        return await loop.run_in_executor(None, _work)
