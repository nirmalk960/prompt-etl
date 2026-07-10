"""Per-job events: persisted in Postgres and broadcast on Redis pubsub.

The router's SSE endpoint subscribes to ``job:{id}`` for live updates and
backfills missed events from Postgres by reading ``JobEvent.seq > last_seen``.
This is what makes SSE survive a browser refresh or transient disconnect.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis_async
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import JobEvent
from ..db.session import session_factory

log = logging.getLogger(__name__)

_redis: redis_async.Redis | None = None


def get_redis() -> redis_async.Redis:
    global _redis
    if _redis is None:
        _redis = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def shutdown_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
    _redis = None


def channel(job_id: uuid.UUID | str) -> str:
    return f"job:{job_id}"


def _json_safe(value: Any) -> Any:
    """Coerce arbitrary Python values into JSON-safe primitives.

    Preview rows may contain `date`/`datetime`/numpy scalar objects depending on
    the engine and source schema. We normalize via JSON round-trip with
    `default=str` so persistence to JSONB and SSE publishing never fails.
    """
    return json.loads(json.dumps(value, default=str))


async def emit(
    *,
    job_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
    session: AsyncSession | None = None,
) -> int:
    """Persist + publish. Returns the new ``seq``.

    Caller can pass an existing session; otherwise we open a short one.
    """
    safe_payload = _json_safe(payload)

    if session is None:
        async with session_factory()() as s:
            seq = await _persist(s, job_id, kind, safe_payload)
            await s.commit()
    else:
        seq = await _persist(session, job_id, kind, safe_payload)
        await session.flush()

    msg = json.dumps({"seq": seq, "kind": kind, "payload": safe_payload})
    try:
        await get_redis().publish(channel(job_id), msg)
    except Exception:
        log.exception("redis publish failed for job %s", job_id)

    return seq


async def _persist(
    session: AsyncSession,
    job_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
) -> int:
    # Compute next seq atomically by counting + 1; uniqueness is enforced by a
    # composite unique index so a race results in a clean retry, not corruption.
    last = await session.execute(
        select(JobEvent.seq).where(JobEvent.job_id == job_id).order_by(JobEvent.seq.desc()).limit(1)
    )
    last_seq = last.scalar() or 0
    seq = last_seq + 1
    session.add(
        JobEvent(
            job_id=job_id,
            seq=seq,
            kind=kind,
            payload=payload,
            created_at=datetime.now(tz=timezone.utc),
        )
    )
    return seq


async def replay_since(
    session: AsyncSession, *, job_id: uuid.UUID, after_seq: int
) -> list[JobEvent]:
    """Return all persisted events for a job after the given seq, in order."""
    rows = await session.execute(
        select(JobEvent)
        .where(JobEvent.job_id == job_id, JobEvent.seq > after_seq)
        .order_by(JobEvent.seq.asc())
    )
    return list(rows.scalars().all())
