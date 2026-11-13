"""Pipeline executor.

The worker calls ``run_pipeline`` with a ``PipelineDefinition``. We:
  1. Topo-sort the DAG (cycle detection).
  2. Resolve each source node to its dataset's parquet object key.
  3. Ask the EngineRouter for the engine to use (auto unless pinned).
  4. For each non-source node, generate code (Ollama or stub), validate it,
     execute on the engine, persist a step checkpoint in object storage, and
     emit a `step_completed` event with the preview.
  5. The first downstream-less "write" node (or the last node) becomes the
     pipeline's `result_dataset` — we materialize a new Dataset row pointing
     at its parquet output.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Dataset, Job
from ..engines import EngineRouter, StepPreview
from ..engines.base import EngineKind
from ..schemas.pipeline import PipelineDefinition
from . import events
from .agent_guard import extract_code, normalize_python_result_assignment, validate
from .object_store import (
    dataset_parquet_key,
    get_object_store,
    step_checkpoint_key,
)
from .ollama_client import OllamaClient, OllamaUnavailable
from .operation_templates import build_prompt, passthrough_code, stub_codegen

log = logging.getLogger(__name__)


@dataclass(slots=True)
class StepArtifact:
    node_id: str
    object_key: str
    row_count: int
    byte_size: int
    preview: StepPreview


class PipelineError(RuntimeError):
    """Raised for any unrecoverable pipeline failure; emit before raising."""


_GROUP_INTENT = re.compile(r"\b(group\s+by|grouped\s+by|month\s*\()", re.IGNORECASE)


def _aggregate_has_grouping(engine: EngineKind, code: str) -> bool:
    if engine == "pandas":
        return ".groupby(" in code
    if engine == "polars":
        return ".group_by(" in code
    if engine == "duckdb":
        return " group by " in f" {code.lower()} "
    return True


# ---------------------------------------------------------------------------
# DAG helpers
# ---------------------------------------------------------------------------


def topological_order(definition: PipelineDefinition) -> list[str]:
    """Kahn's algorithm. Raises ``PipelineError`` on a cycle."""
    indeg: dict[str, int] = {n.id: 0 for n in definition.nodes}
    children: dict[str, list[str]] = defaultdict(list)
    for e in definition.edges:
        indeg[e.target] += 1
        children[e.source].append(e.target)
    ready = [nid for nid, d in indeg.items() if d == 0]
    order: list[str] = []
    while ready:
        # Sort for determinism — useful for tests and SSE event order.
        ready.sort()
        nid = ready.pop(0)
        order.append(nid)
        for c in children[nid]:
            indeg[c] -= 1
            if indeg[c] == 0:
                ready.append(c)
    if len(order) != len(definition.nodes):
        raise PipelineError("pipeline contains a cycle")
    return order


def parents_of(definition: PipelineDefinition) -> dict[str, list[str]]:
    parents: dict[str, list[str]] = defaultdict(list)
    for e in definition.edges:
        parents[e.target].append(e.source)
    return parents


# ---------------------------------------------------------------------------
# Engine selection
# ---------------------------------------------------------------------------


async def _total_input_bytes(
    session: AsyncSession,
    definition: PipelineDefinition,
) -> int:
    total = 0
    for n in definition.nodes:
        if n.kind == "source" and n.dataset_id is not None:
            ds = await session.get(Dataset, n.dataset_id)
            if ds and ds.parquet_size_bytes:
                total += ds.parquet_size_bytes
    return total


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------


async def _gen_code(
    *,
    engine: EngineKind,
    node_kind: str,
    user_prompt: str,
    schema: list[dict[str, str]],
    ollama: OllamaClient,
) -> str:
    # Always try the deterministic stub first. It's instant, free, and
    # produces well-formed code for the common operations.
    stub = stub_codegen(
        engine=engine, node_kind=node_kind, user_prompt=user_prompt, schema=schema
    )
    if stub is not None:
        return stub

    # Fall back to the LLM.
    bundle = build_prompt(
        engine=engine, node_kind=node_kind, user_prompt=user_prompt, schema=schema
    )
    try:
        raw = await ollama.generate(
            prompt=bundle.user, system=bundle.system, stop=bundle.stop
        )
    except OllamaUnavailable as e:
        raise PipelineError(
            f"No deterministic match for prompt and Ollama is unavailable: {e}"
        ) from e
    code = extract_code(raw)
    if engine in {"pandas", "polars"}:
        code = normalize_python_result_assignment(code)
    # Never substitute a passthrough here: it would report success while
    # silently skipping the requested transformation.
    if not code.strip():
        raise PipelineError(
            f"The model returned no code for {node_kind} prompt: {user_prompt!r}. "
            "Rephrase the prompt or try again."
        )
    return code


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


async def run_pipeline(
    *,
    session: AsyncSession,
    job: Job,
    router: EngineRouter,
    ollama: OllamaClient,
) -> Dataset | None:
    """Execute the pipeline associated with ``job``. Emits SSE events.

    Returns the result Dataset row (or None if the pipeline had no terminal
    output, e.g. only source nodes).
    """
    definition = PipelineDefinition.model_validate(job.definition)

    # Decide engine.
    total_bytes = await _total_input_bytes(session, definition)
    node_kinds = [n.kind for n in definition.nodes if n.kind not in {"source", "write"}]
    decision = router.decide(
        total_input_bytes=total_bytes,
        node_kinds=node_kinds,
        pinned=None if definition.engine == "auto" else definition.engine,
    )
    if decision.engine == "spark":
        raise PipelineError(
            f"Pipeline routed to Spark engine but Phase 2 is not yet implemented "
            f"(detected {total_bytes:,} bytes). Increase ENGINE_SPARK_THRESHOLD_BYTES "
            f"or pin engine: pandas|polars|duckdb in the pipeline definition."
        )
    engine = router.get(decision.engine)
    job.engine = decision.engine
    await session.flush()
    await events.emit(
        job_id=job.id,
        kind="engine_selected",
        payload={
            "engine": decision.engine,
            "reason": decision.reason,
            "detected_ram_bytes": decision.detected_ram_bytes,
            "total_input_bytes": total_bytes,
        },
        session=session,
    )
    await session.commit()

    # Pre-load source datasets so we know their parquet keys and schemas.
    sources: dict[str, Dataset] = {}
    for n in definition.nodes:
        if n.kind == "source":
            if n.dataset_id is None:
                raise PipelineError(f"source node {n.id} is missing dataset_id")
            ds = await session.get(Dataset, n.dataset_id)
            if ds is None or ds.parquet_object_key is None:
                raise PipelineError(
                    f"source node {n.id}: dataset {n.dataset_id} is not ready"
                )
            sources[n.id] = ds

    # Execute in topological order, tracking each node's output object key.
    order = topological_order(definition)
    parents = parents_of(definition)
    artifacts: dict[str, StepArtifact] = {}
    write_results: list[StepArtifact] = []
    workspace_id = str(job.workspace_id)
    job_id = str(job.id)

    node_by_id = {n.id: n for n in definition.nodes}

    for nid in order:
        node = node_by_id[nid]
        await events.emit(
            job_id=job.id,
            kind="step_started",
            payload={"node_id": nid, "node_kind": node.kind, "label": node.label},
            session=session,
        )
        await session.commit()

        if node.kind == "source":
            ds = sources[nid]
            assert ds.parquet_object_key is not None
            preview = await engine.preview(object_key=ds.parquet_object_key, n=20)
            artifacts[nid] = StepArtifact(
                node_id=nid,
                object_key=ds.parquet_object_key,
                row_count=ds.row_count or 0,
                byte_size=ds.parquet_size_bytes or 0,
                preview=preview,
            )
            await events.emit(
                job_id=job.id,
                kind="step_completed",
                payload={
                    "node_id": nid,
                    "row_count": artifacts[nid].row_count,
                    "byte_size": artifacts[nid].byte_size,
                    "preview": _preview_dict(preview),
                },
                session=session,
            )
            await session.commit()
            continue

        # Resolve inputs from parent artifacts.
        ps = parents.get(nid, [])
        if not ps:
            raise PipelineError(f"node {nid} has no inputs")
        input_keys = [artifacts[p].object_key for p in ps]
        input_schema = artifacts[ps[0]].preview.schema

        # Write nodes: just promote the upstream output as the result (Phase 1
        # writes to a Parquet checkpoint; exporting CSV/JSON is a separate
        # /export endpoint). Stash for `result_dataset` materialization.
        if node.kind == "write":
            artifact = artifacts[ps[0]]
            write_results.append(artifact)
            await events.emit(
                job_id=job.id,
                kind="step_completed",
                payload={
                    "node_id": nid,
                    "row_count": artifact.row_count,
                    "byte_size": artifact.byte_size,
                    "preview": _preview_dict(artifact.preview),
                    "promoted_from": ps[0],
                },
                session=session,
            )
            await session.commit()
            artifacts[nid] = artifact
            continue

        # Transform node: generate code, validate, run.
        prompt = (node.prompt or "").strip()
        if not prompt:
            # Custom node with no prompt? Treat as passthrough.
            code = passthrough_code(decision.engine)
        else:
            code = await _gen_code(
                engine=decision.engine,
                node_kind=node.kind,
                user_prompt=prompt,
                schema=input_schema,
                ollama=ollama,
            )

        validation_error = validate(decision.engine, code)
        if validation_error is not None:
            raise PipelineError(
                f"node {nid} generated code failed validation: {validation_error}\n--\n{code}"
            )
        if (
            node.kind == "aggregate"
            and _GROUP_INTENT.search(prompt)
            and not _aggregate_has_grouping(decision.engine, code)
        ):
            raise PipelineError(
                "aggregate prompt requested grouped output, but generated code did not include grouping "
                f"for engine={decision.engine}\n--\n{code}"
            )

        out_key = step_checkpoint_key(workspace_id, job_id, nid)
        result = await engine.execute_step(
            node_id=nid,
            node_kind=node.kind,
            code=code,
            input_object_keys=input_keys,
            output_object_key=out_key,
            config=node.config,
        )
        artifact = StepArtifact(
            node_id=nid,
            object_key=result.output_object_key,
            row_count=result.row_count,
            byte_size=result.byte_size,
            preview=result.preview,
        )
        artifacts[nid] = artifact

        await events.emit(
            job_id=job.id,
            kind="step_completed",
            payload={
                "node_id": nid,
                "row_count": artifact.row_count,
                "byte_size": artifact.byte_size,
                "preview": _preview_dict(artifact.preview),
                "code": code,
                "engine": decision.engine,
            },
            session=session,
        )
        await session.commit()

    # Materialize the result dataset, if any.
    chosen = write_results[0] if write_results else (
        StepArtifact(
            node_id=order[-1],
            object_key=artifacts[order[-1]].object_key,
            row_count=artifacts[order[-1]].row_count,
            byte_size=artifacts[order[-1]].byte_size,
            preview=artifacts[order[-1]].preview,
        )
        if artifacts
        else None
    )
    if chosen is None:
        return None

    # Copy the chosen checkpoint to a stable dataset key so the result outlives
    # the job's step checkpoints (which can be GC'd).
    new_ds_id = uuid.uuid4()
    final_key = dataset_parquet_key(workspace_id, str(new_ds_id))
    store = get_object_store()
    # Use the underlying download/upload pair; cheap for typical result sizes.
    import os
    import tempfile

    with tempfile.TemporaryDirectory(prefix="prompt-etl-final-") as tmp:
        local = os.path.join(tmp, "result.parquet")
        store.download_to_file(chosen.object_key, local)
        store.upload_file(local, final_key, content_type="application/octet-stream")

    result_dataset = Dataset(
        id=new_ds_id,
        workspace_id=job.workspace_id,
        name=f"Job {str(job.id)[:8]} result",
        source_format="parquet",
        raw_object_key=final_key,
        parquet_object_key=final_key,
        raw_size_bytes=chosen.byte_size,
        parquet_size_bytes=chosen.byte_size,
        row_count=chosen.row_count,
        schema_json={"fields": chosen.preview.schema},
        status="ready",
    )
    session.add(result_dataset)
    job.result_dataset_id = new_ds_id
    await session.commit()

    await events.emit(
        job_id=job.id,
        kind="result_dataset_ready",
        payload={"dataset_id": str(new_ds_id), "row_count": chosen.row_count},
        session=session,
    )
    await session.commit()
    return result_dataset


def _preview_dict(p: StepPreview) -> dict[str, Any]:
    return {
        "columns": p.columns,
        "rows": p.rows[:20],  # cap on the wire
        "row_count": p.row_count,
        "bytes": p.bytes,
        "schema": p.schema,
    }
