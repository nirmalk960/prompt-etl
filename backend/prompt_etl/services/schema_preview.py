"""Predict every node's output columns without running the pipeline.

Used by the canvas for preflight checks and column pickers.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Dataset
from ..ops import OpError, get_op
from ..ops.runner import dry_run_schema
from ..schemas.pipeline import PipelineDefinition
from .object_store import get_object_store
from .pipeline import PipelineError, parents_of, topological_order

# Parquet physical types, stored by ingest before 0.2.0. They can't tell a
# date from a string, so such datasets get their schema re-read once.
_PHYSICAL = {"BOOLEAN", "INT32", "INT64", "INT96", "FLOAT", "DOUBLE", "BYTE_ARRAY",
             "FIXED_LEN_BYTE_ARRAY"}


def _read_arrow_schema(object_key: str) -> list[dict[str, str]]:
    import pyarrow.parquet as pq

    with tempfile.TemporaryDirectory(prefix="prompt-etl-schema-") as tmp:
        local = os.path.join(tmp, "d.parquet")
        get_object_store().download_to_file(object_key, local)
        return [{"name": f.name, "dtype": str(f.type)} for f in pq.read_schema(local)]


async def dataset_fields(session: AsyncSession, ds: Dataset) -> list[dict[str, str]]:
    fields = list((ds.schema_json or {}).get("fields") or [])
    if ds.parquet_object_key and (not fields or any(f.get("dtype") in _PHYSICAL for f in fields)):
        fields = await asyncio.get_running_loop().run_in_executor(
            None, _read_arrow_schema, ds.parquet_object_key
        )
        ds.schema_json = {**(ds.schema_json or {}), "columns": [f["name"] for f in fields],
                          "fields": fields}
        await session.flush()
    return fields


def _as_fields(schema: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"name": n, "dtype": t} for n, t in schema]


def _as_schema(fields: list[dict[str, str]]) -> list[tuple[str, str]]:
    return [(f["name"], f.get("dtype") or "VARCHAR") for f in fields]


async def preview_schemas(
    session: AsyncSession, workspace_id: Any, definition: PipelineDefinition
) -> dict[str, dict[str, Any]]:
    """``{node_id: {"columns": [{name, dtype}] | None, "error": str | None}}``.

    ``columns`` is None when the output can't be known before running
    (prompt steps, pivots, or anything downstream of them).
    """
    try:
        order = topological_order(definition)
    except PipelineError as e:
        return {n.id: {"columns": None, "error": str(e)} for n in definition.nodes}
    parents = parents_of(definition)
    nodes = {n.id: n for n in definition.nodes}
    out: dict[str, dict[str, Any]] = {}

    for nid in order:
        node = nodes[nid]
        if node.kind == "source":
            ds = await session.get(Dataset, node.dataset_id) if node.dataset_id else None
            if ds is None or ds.workspace_id != workspace_id:
                out[nid] = {"columns": None, "error": "Pick a dataset for this source"}
            elif ds.status != "ready" or not ds.parquet_object_key:
                out[nid] = {"columns": None, "error": "This dataset is not ready yet"}
            else:
                out[nid] = {"columns": await dataset_fields(session, ds), "error": None}
            continue

        ps = parents.get(nid, [])
        if not ps:
            out[nid] = {"columns": None, "error": "Connect an input"}
            continue
        ins = [out[p]["columns"] for p in ps]
        if any(c is None for c in ins):
            out[nid] = {"columns": None, "error": None}
            continue
        if node.kind == "write":
            out[nid] = {"columns": ins[0], "error": None}
            continue

        spec = get_op(node.kind)
        if spec is None or node.effective_mode == "prompt":
            out[nid] = {"columns": None, "error": None}
            continue
        try:
            schema = dry_run_schema(spec, node.config, [_as_schema(c) for c in ins])
            out[nid] = {"columns": None if schema is None else _as_fields(schema), "error": None}
        except OpError as e:
            out[nid] = {"columns": None, "error": str(e)}
        except Exception as e:  # noqa: BLE001 - a bad config must not break the whole preview
            out[nid] = {"columns": None, "error": f"Invalid settings: {e}"}
    return out
