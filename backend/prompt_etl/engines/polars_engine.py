"""Polars engine.

LLM-generated code uses Polars' lazy API. Convention:
    - inputs[i] is a polars.LazyFrame (built with pl.scan_parquet)
    - df is inputs[0]
    - result must be a LazyFrame OR a DataFrame
The engine calls .collect(streaming=True) and writes parquet.
"""

from __future__ import annotations

import asyncio
import builtins
from typing import Any

import polars as pl

from .base import DataEngine, StepPreview, StepResult
from ._io import staged_inputs, staged_output, upload_output

_SAFE_BUILTINS = {
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "frozenset",
    "int", "isinstance", "issubclass", "len", "list", "map", "max", "min", "next",
    "print", "range", "reversed", "round", "set", "slice", "sorted", "str", "sum",
    "tuple", "type", "zip",
}


def _collect_streaming(lf: pl.LazyFrame) -> pl.DataFrame:
    """Streaming collect across polars versions.

    polars >= 1.25 deprecates ``collect(streaming=True)`` in favor of
    ``collect(engine="streaming")``. We try the new API first.
    """
    try:
        return lf.collect(engine="streaming")
    except TypeError:
        return lf.collect(streaming=True)


def _restricted_namespace(inputs: list[pl.LazyFrame]) -> dict[str, Any]:
    safe = {k: getattr(builtins, k) for k in _SAFE_BUILTINS if hasattr(builtins, k)}
    return {
        "__builtins__": safe,
        "pl": pl,
        "inputs": inputs,
        "df": inputs[0] if inputs else None,
    }


def _run_sync(code: str, input_paths: list[str], output_path: str) -> tuple[int, int]:
    inputs = [pl.scan_parquet(p) for p in input_paths]
    ns = _restricted_namespace(inputs)
    exec(code, ns)  # noqa: S102 - sandboxed via _restricted_namespace
    result = ns.get("result")
    if result is None:
        raise RuntimeError("polars step did not assign `result`")
    if isinstance(result, pl.LazyFrame):
        df = _collect_streaming(result)
    elif isinstance(result, pl.DataFrame):
        df = result
    else:
        raise RuntimeError(
            f"polars step `result` must be LazyFrame or DataFrame, got {type(result).__name__}"
        )
    df.write_parquet(output_path, compression="zstd")
    return df.height, df.width


class PolarsEngine(DataEngine):
    kind = "polars"

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
                df = pl.read_parquet(out_path).head(50)
                preview = StepPreview(
                    columns=df.columns,
                    rows=df.to_numpy().tolist(),
                    row_count=rows,
                    bytes=size,
                    schema=[{"name": c, "dtype": str(df.schema[c])} for c in df.columns],
                )
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
                df = pl.read_parquet(paths[0]).head(n)
                return StepPreview(
                    columns=df.columns,
                    rows=df.to_numpy().tolist(),
                    row_count=df.height,
                    schema=[{"name": c, "dtype": str(df.schema[c])} for c in df.columns],
                )

        return await loop.run_in_executor(None, _work)
