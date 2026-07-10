"""Jobs API.

POST /jobs               - enqueue a pipeline run; returns {job_id}.
GET  /jobs               - list recent jobs.
GET  /jobs/{id}          - job detail with persisted events.
GET  /jobs/{id}/events   - SSE stream. Resumable via ``Last-Event-ID`` header
                           or ``after`` query param.
POST /jobs/{id}/cancel   - best-effort cancellation (Phase 1 marks status only).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from ..arq_pool import get_arq_pool
from ..db.models import Job, JobEvent
from ..deps import CurrentWorkspace, SessionDep
from ..db.session import session_factory
from ..schemas.job import JobCreate, JobDetail, JobSummary
from ..schemas.pipeline import PipelineDefinition
from ..services import events as event_service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobSummary, status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreate,
    session: SessionDep,
    workspace: CurrentWorkspace,
) -> JobSummary:
    job = Job(
        workspace_id=workspace.id,
        workflow_id=body.workflow_id,
        definition=body.definition.model_dump(mode="json"),
        status="queued",
    )
    session.add(job)
    await session.flush()
    await session.commit()
    pool = await get_arq_pool()
    await pool.enqueue_job("run_pipeline_job", str(job.id))
    return _to_summary(job)


@router.get("", response_model=list[JobSummary])
async def list_jobs(
    session: SessionDep,
    workspace: CurrentWorkspace,
    limit: int = Query(default=50, le=200),
) -> list[JobSummary]:
    rows = await session.execute(
        select(Job)
        .where(Job.workspace_id == workspace.id)
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    return [_to_summary(j) for j in rows.scalars()]


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(
    session: SessionDep,
    workspace: CurrentWorkspace,
    job_id: uuid.UUID,
) -> JobDetail:
    job = await session.get(Job, job_id)
    if job is None or job.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="job not found")
    rows = await session.execute(
        select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.seq.asc())
    )
    return JobDetail(
        **_to_summary(job).model_dump(),
        definition=PipelineDefinition.model_validate(job.definition),
        events=[
            {
                "seq": e.seq,
                "kind": e.kind,
                "payload": e.payload,
                "created_at": e.created_at.isoformat(),
            }
            for e in rows.scalars()
        ],
    )


@router.post("/{job_id}/cancel", response_model=JobSummary)
async def cancel_job(
    session: SessionDep,
    workspace: CurrentWorkspace,
    job_id: uuid.UUID,
) -> JobSummary:
    job = await session.get(Job, job_id)
    if job is None or job.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status in {"succeeded", "failed", "cancelled"}:
        return _to_summary(job)
    # Phase 1: best-effort; the worker checks status between steps in Phase 2.
    job.status = "cancelled"
    await session.flush()
    await event_service.emit(
        job_id=job.id, kind="job_cancelled", payload={}, session=session
    )
    return _to_summary(job)


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------


@router.get("/{job_id}/events")
async def stream_events(
    request: Request,
    job_id: uuid.UUID,
    after: int = Query(default=0, ge=0, description="last seen event seq"),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """SSE stream for a job.

    Resumes from ``after`` (query) or the standard ``Last-Event-ID`` header,
    whichever is greater. Backfills missed persisted events, then subscribes
    to the Redis pubsub channel for live updates.
    """
    resume_from = after
    if last_event_id is not None:
        try:
            resume_from = max(resume_from, int(last_event_id))
        except ValueError:
            pass

    async def event_gen() -> AsyncIterator[dict]:
        SessionLocal = session_factory()
        # Backfill from Postgres.
        async with SessionLocal() as s:
            backfill = await event_service.replay_since(
                s, job_id=job_id, after_seq=resume_from
            )
        latest_seq = resume_from
        for ev in backfill:
            latest_seq = ev.seq
            yield {
                "id": str(ev.seq),
                "event": ev.kind,
                "data": json.dumps(
                    {"seq": ev.seq, "kind": ev.kind, "payload": ev.payload}
                ),
            }

        # Then subscribe to live updates.
        r = event_service.get_redis()
        pubsub = r.pubsub()
        await pubsub.subscribe(event_service.channel(job_id))
        try:
            while True:
                if await request.is_disconnected():
                    return
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=5.0
                )
                if msg is None:
                    yield {"event": "ping", "data": "{}"}
                    continue
                data = msg.get("data")
                if not data:
                    continue
                try:
                    parsed = json.loads(data)
                except Exception:
                    continue
                seq = parsed.get("seq", 0)
                if seq <= latest_seq:
                    continue
                latest_seq = seq
                yield {
                    "id": str(seq),
                    "event": parsed.get("kind", "message"),
                    "data": data,
                }
        finally:
            await pubsub.unsubscribe(event_service.channel(job_id))
            await pubsub.aclose()

    return EventSourceResponse(event_gen())


def _to_summary(job: Job) -> JobSummary:
    return JobSummary(
        id=job.id,
        status=job.status,
        engine=job.engine,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        result_dataset_id=job.result_dataset_id,
    )
