"""FastAPI app entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .arq_pool import get_arq_pool, shutdown_arq_pool
from .config import get_settings
from .db.session import init_engine, shutdown_engine
from .logging_setup import configure_logging
from .routers import auth, datasets, health, jobs, operations, pipelines, uploads, workflows
from .services.events import shutdown_redis
from .services.object_store import get_object_store

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    init_engine()
    try:
        store = get_object_store()
        store.ensure_bucket()
    except Exception:
        log.exception("object store bucket bootstrap failed (continuing)")
    await get_arq_pool()
    log.info("API startup complete env=%s", settings.app_env)
    yield
    await shutdown_arq_pool()
    await shutdown_redis()
    await shutdown_engine()
    log.info("API shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Prompt ETL API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(datasets.router)
    app.include_router(uploads.router)
    app.include_router(workflows.router)
    app.include_router(jobs.router)
    app.include_router(operations.router)
    app.include_router(pipelines.router)
    _mount_dashboards(app)
    return app


def _mount_dashboards(app: FastAPI) -> None:
    try:
        from a2wsgi import WSGIMiddleware

        from .dashboards.app import create_dash_app
    except ImportError:
        log.warning("dash/a2wsgi not installed; dashboards are disabled")
        return
    app.mount("/dash", WSGIMiddleware(create_dash_app().server))


app = create_app()
