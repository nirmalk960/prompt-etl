"""Arq tasks: ingest a raw upload to Parquet, run a pipeline job.

Tasks own the DB session lifecycle and update job/dataset rows with progress.
All long-running work happens here, not in the API process.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..db.models import Dataset, Job
from ..db.session import session_factory
from ..engines import EngineRouter
from ..services import events
from ..services.ingest import ingest_to_parquet
from ..services.object_store import dataset_parquet_key, get_object_store
from ..services.ollama_client import OllamaClient
from ..services.pipeline import run_pipeline

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ingest task
# ---------------------------------------------------------------------------


async def ingest_dataset(ctx: dict, dataset_id: str) -> dict[str, Any]:
    """Convert a freshly-uploaded raw file to Parquet."""
    SessionLocal = session_factory()
    async with SessionLocal() as session:
        ds = await session.get(Dataset, uuid.UUID(dataset_id))
        if ds is None:
            log.warning("ingest_dataset: dataset %s not found", dataset_id)
            return {"status": "missing"}

        ds.status = "ingesting"
        await session.commit()

        try:
            parquet_key = dataset_parquet_key(str(ds.workspace_id), str(ds.id))
            result = ingest_to_parquet(
                raw_object_key=ds.raw_object_key,
                parquet_object_key=parquet_key,
                source_format=ds.source_format,
            )
            ds.parquet_object_key = result.parquet_object_key
            ds.parquet_size_bytes = result.parquet_size_bytes
            ds.row_count = result.row_count
            ds.schema_json = {"fields": result.schema}
            ds.status = "ready"
            ds.error = None
            await session.commit()
            log.info("ingest_dataset %s ok rows=%s", dataset_id, result.row_count)
            return {
                "status": "ready",
                "row_count": result.row_count,
                "parquet_object_key": result.parquet_object_key,
            }
        except Exception as e:
            log.exception("ingest_dataset %s failed", dataset_id)
            ds.status = "failed"
            ds.error = str(e)[:2000]
            await session.commit()
            return {"status": "failed", "error": str(e)}


# ---------------------------------------------------------------------------
# Pipeline run task
# ---------------------------------------------------------------------------


_router: EngineRouter | None = None


def _get_router() -> EngineRouter:
    global _router
    if _router is None:
        _router = EngineRouter()
    return _router


async def run_pipeline_job(ctx: dict, job_id: str) -> dict[str, Any]:
    SessionLocal = session_factory()
    async with SessionLocal() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        if job is None:
            log.warning("run_pipeline_job: job %s not found", job_id)
            return {"status": "missing"}

        job.status = "running"
        job.started_at = datetime.now(tz=timezone.utc)
        await session.commit()

        await events.emit(
            job_id=job.id, kind="job_started", payload={}, session=session
        )
        await session.commit()

        ollama = OllamaClient()
        try:
            result_ds = await run_pipeline(
                session=session, job=job, router=_get_router(), ollama=ollama
            )
            job.status = "succeeded"
            job.finished_at = datetime.now(tz=timezone.utc)
            job.error = None
            await session.commit()
            await events.emit(
                job_id=job.id,
                kind="job_succeeded",
                payload={
                    "result_dataset_id": str(result_ds.id) if result_ds else None,
                    "engine": job.engine,
                },
                session=session,
            )
            await session.commit()
            return {
                "status": "succeeded",
                "result_dataset_id": str(result_ds.id) if result_ds else None,
            }
        except Exception as e:
            log.exception("run_pipeline_job %s failed", job_id)
            # If the preceding failure happened during flush/commit, this
            # session is left in "pending rollback" state. Reset it before we
            # attempt to persist terminal job status, otherwise the job appears
            # stuck in "running".
            await session.rollback()
            job = await session.get(Job, uuid.UUID(job_id))
            if job is None:
                return {"status": "failed", "error": str(e)}

            job.status = "failed"
            job.finished_at = datetime.now(tz=timezone.utc)
            job.error = str(e)[:4000]
            await session.commit()
            await events.emit(
                job_id=job.id,
                kind="job_failed",
                payload={"error": str(e)[:2000]},
                session=session,
            )
            await session.commit()
            return {"status": "failed", "error": str(e)}
