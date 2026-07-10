"""pandas engine.

Designed for small data (< RAM/4 by default). LLM-generated code is exec'd
in a restricted namespace where:
    - inputs[i] is a DataFrame
    - df is inputs[0] (single-input convenience)
    - result must be assigned to `result` as a DataFrame
"""

from __future__ import annotations

import asyncio
import builtins
from typing import Any

import pandas as pd

from .base import DataEngine, StepPreview, StepResult
from ._io import staged_inputs, staged_output, upload_output

_SAFE_BUILTINS = {
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "frozenset",
    "int", "isinstance", "issubclass", "len", "list", "map", "max", "min", "next",
    "print", "range", "reversed", "round", "set", "slice", "sorted", "str", "sum",
    "tuple", "type", "zip",
}


def _restricted_namespace(inputs: list[pd.DataFrame]) -> dict[str, Any]:
    safe = {k: getattr(builtins, k) for k in _SAFE_BUILTINS if hasattr(builtins, k)}
    ns: dict[str, Any] = {
        "__builtins__": safe,
        "pd": pd,
        "inputs": inputs,
        "df": inputs[0] if inputs else None,
    }
    return ns


def _run_sync(
    code: str,
    input_paths: list[str],
    output_path: str,
) -> tuple[int, int]:
    inputs = [pd.read_parquet(p) for p in input_paths]
    ns = _restricted_namespace(inputs)
    exec(code, ns)  # noqa: S102 - intentionally exec; sandboxed via _restricted_namespace
    result = ns.get("result")
    if not isinstance(result, pd.DataFrame):
        raise RuntimeError(
            "pandas step did not assign a DataFrame to `result` "
            f"(got {type(result).__name__})"
        )
    result.to_parquet(output_path, engine="pyarrow", compression="zstd", index=False)
    return len(result), len(result.columns)


class PandasEngine(DataEngine):
    kind = "pandas"

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
                df = pd.read_parquet(out_path).head(50)
                preview = StepPreview(
                    columns=[str(c) for c in df.columns],
                    rows=df.astype(object).where(df.notnull(), None).values.tolist(),
                    row_count=rows,
                    bytes=size,
                    schema=[
                        {"name": str(c), "dtype": str(df[c].dtype)} for c in df.columns
                    ],
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
                df = pd.read_parquet(paths[0]).head(n)
                return StepPreview(
                    columns=[str(c) for c in df.columns],
                    rows=df.astype(object).where(df.notnull(), None).values.tolist(),
                    row_count=len(df),
                    schema=[{"name": str(c), "dtype": str(df[c].dtype)} for c in df.columns],
                )

        return await loop.run_in_executor(None, _work)
