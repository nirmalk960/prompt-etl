"""Health + ready endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from ..config import get_settings
from ..engines.resources import detect_available_memory_bytes, detect_cpu_count
from ..services.ollama_client import OllamaClient

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict:
    settings = get_settings()
    ram = detect_available_memory_bytes(settings.engine_ram_override_bytes)
    cpus = detect_cpu_count()
    ollama = OllamaClient()
    ollama_ok = await ollama.is_available()
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "ram_bytes": ram,
        "cpus": cpus,
        "ollama": {
            "available": ollama_ok,
            "model": ollama.model,
            "disabled": ollama.disabled,
        },
        "object_store": settings.object_store_kind,
        "spark_threshold_bytes": settings.engine_spark_threshold_bytes,
    }
