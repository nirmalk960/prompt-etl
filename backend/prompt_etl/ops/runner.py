"""Execute configured ops, and dry-run them for preflight schemas."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

import duckdb

from ..engines._io import staged_inputs, staged_output, upload_output
from ..engines.base import StepPreview, StepResult
from .base import OpError, OpSpec, Schema
from .sqlsafe import ident, lit, to_duckdb_type


@dataclass(slots=True)
class OpRun:
    result: StepResult
    code: str
    engine: str
    metrics: dict[str, Any] | None = None


def sandboxed_connection(allowed_dirs: list[str]) -> duckdb.DuckDBPyConnection:
    """A connection that can only touch files under ``allowed_dirs``."""
    con = duckdb.connect()
    dirs = "[" + ", ".join(lit(d.rstrip("/") + "/") for d in allowed_dirs) + "]"
    con.execute("SET threads = 4")
    if allowed_dirs:
        con.execute(f"SET temp_directory = {lit(os.path.join(allowed_dirs[-1], 'spill'))}")
        con.execute(f"SET allowed_directories = {dirs}")
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def _register_inputs(con: duckdb.DuckDBPyConnection, input_paths: list[str]) -> list[Schema]:
    schemas: list[Schema] = []
    for i, p in enumerate(input_paths):
        con.execute(f"CREATE VIEW input_{i} AS SELECT * FROM read_parquet({lit(p)})")
        con.execute(
            f"CREATE VIEW input_{i}_rn AS SELECT * EXCLUDE (file_row_number), "
            f"file_row_number AS __rn FROM read_parquet({lit(p)}, file_row_number = true)"
        )
        schemas.append([(r[0], r[1]) for r in con.execute(f"DESCRIBE input_{i}").fetchall()])
    if input_paths:
        con.execute("CREATE VIEW df AS SELECT * FROM input_0")
    return schemas


def _preview(out_path: str, rows: int, size: int) -> StepPreview:
    con = duckdb.connect()
    try:
        head = con.execute(f"SELECT * FROM read_parquet({lit(out_path)}) LIMIT 50").df()
        schema = [(r[0], r[1]) for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet({lit(out_path)})").fetchall()]
    finally:
        con.close()
    safe = head.astype(object).where(head.notnull(), None) if len(head) else head
    return StepPreview(
        columns=[str(c) for c in head.columns],
        rows=safe.values.tolist() if len(head) else [],
        row_count=rows,
        bytes=size,
        schema=[{"name": n, "dtype": t} for n, t in schema],
    )


def _check_inputs(spec: OpSpec, n: int) -> None:
    if spec.inputs == 2 and n != 2:
        raise OpError(f"{spec.label} needs two inputs: connect one to 'left' and one to 'right'")
    if spec.inputs == 1 and n != 1:
        raise OpError(f"{spec.label} takes exactly one input (got {n})")


def _run_sql_sync(spec: OpSpec, cfg: dict[str, Any], in_paths: list[str], out_path: str) -> str:
    assert spec.compile is not None
    dirs = sorted({os.path.dirname(p) for p in in_paths} | {os.path.dirname(out_path)})
    con = sandboxed_connection(dirs)
    try:
        schemas = _register_inputs(con, in_paths)
        sql = spec.compile(cfg, schemas)
        if spec.post_check is not None:
            spec.post_check(con, cfg, schemas)
        try:
            con.execute(f"COPY ({sql}) TO {lit(out_path)} (FORMAT PARQUET, COMPRESSION ZSTD)")
        except duckdb.Error as e:
            raise OpError(_clean_duckdb_error(e)) from e
        return sql
    finally:
        con.close()


def _run_python_sync(spec: OpSpec, cfg: dict[str, Any], in_paths: list[str], out_path: str):
    import pandas as pd

    assert spec.run is not None
    dfs = [pd.read_parquet(p) for p in in_paths]
    res = spec.run(dfs, cfg)
    res.df.columns = [str(c) for c in res.df.columns]
    res.df.to_parquet(out_path, engine="pyarrow", compression="zstd", index=False)
    return res


def _clean_duckdb_error(e: Exception) -> str:
    msg = str(e).strip()
    for prefix in ("Binder Error: ", "Conversion Error: ", "Invalid Input Error: ", "Parser Error: "):
        if msg.startswith(prefix):
            msg = msg[len(prefix):]
    return msg.split("\n\nLINE")[0].strip()


async def run_op(
    spec: OpSpec, cfg: dict[str, Any], input_object_keys: list[str], output_object_key: str
) -> OpRun:
    _check_inputs(spec, len(input_object_keys))
    loop = asyncio.get_running_loop()

    def _work() -> OpRun:
        with staged_inputs(input_object_keys) as in_paths, staged_output() as out_path:
            metrics = None
            if spec.engine == "sql":
                code = _run_sql_sync(spec, cfg, in_paths, out_path)
                engine = "duckdb"
            elif spec.engine == "python":
                res = _run_python_sync(spec, cfg, in_paths, out_path)
                code = f"# Python operation: {spec.label}\n# settings: {cfg}"
                metrics = res.metrics
                engine = "python"
            else:
                raise OpError(f"{spec.label} only runs in prompt mode")
            size, rows = upload_output(out_path, output_object_key)
            preview = _preview(out_path, rows, size)
            return OpRun(
                result=StepResult(output_object_key=output_object_key, row_count=rows,
                                  byte_size=size, preview=preview),
                code=code, engine=engine, metrics=metrics,
            )

    return await loop.run_in_executor(None, _work)


# ---------------------------------------------------------------------------
# Preflight dry run
# ---------------------------------------------------------------------------


def dry_run_schema(spec: OpSpec, cfg: dict[str, Any], schemas: list[Schema]) -> Schema | None:
    """Predict the output columns without data. ``None`` = unknown until run."""
    _check_inputs(spec, len(schemas))
    if spec.engine == "python":
        assert spec.output_schema is not None
        return spec.output_schema(cfg, schemas)
    if spec.engine != "sql":
        return None
    assert spec.compile is not None
    con = duckdb.connect()
    try:
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        typed: list[Schema] = []
        for i, schema in enumerate(schemas):
            cols = [(n, to_duckdb_type(t)) for n, t in schema]
            body = ", ".join(f"{ident(n)} {t}" for n, t in cols) or '"_empty" INTEGER'
            con.execute(f"CREATE TABLE input_{i} ({body})")
            con.execute(f"CREATE VIEW input_{i}_rn AS SELECT *, 0::BIGINT AS __rn FROM input_{i}")
            typed.append(cols)
        con.execute("CREATE VIEW df AS SELECT * FROM input_0")
        sql = spec.compile(cfg, typed)
        if spec.dynamic_schema:
            return None
        try:
            rows = con.execute(f"DESCRIBE {sql}").fetchall()
        except duckdb.Error as e:
            raise OpError(_clean_duckdb_error(e)) from e
        return [(r[0], r[1]) for r in rows]
    finally:
        con.close()
