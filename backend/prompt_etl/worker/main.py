"""Arq worker entrypoint.

Run with:
    arq prompt_etl.worker.main.WorkerSettings
"""

from __future__ import annotations

import logging

from arq.connections import RedisSettings

from ..config import get_settings
from ..db.session import init_engine, shutdown_engine
from ..logging_setup import configure_logging
from . import tasks

log = logging.getLogger(__name__)


async def startup(ctx: dict) -> None:
    configure_logging()
    init_engine()
    log.info("worker startup complete")


async def shutdown(ctx: dict) -> None:
    await shutdown_engine()
    log.info("worker shutdown complete")


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


class WorkerSettings:
    """Arq settings object discovered by the `arq` CLI."""

    functions = [tasks.ingest_dataset, tasks.run_pipeline_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings()
    max_jobs = get_settings().arq_max_jobs
    job_timeout = get_settings().arq_job_timeout_seconds
    keep_result = 3600
