"""Application settings loaded from environment variables.

Uses pydantic-settings so the same Settings object works in tests
(via constructor overrides) and in production (via env).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_env: str = "local"
    log_level: str = "INFO"
    secret_key: str = "dev-secret-change-me"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_allow_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Public URL prefix the browser uses for dashboards (nginx strips /api).
    dash_path_prefix: str = "/api/dash/"

    # DB
    database_url: str = (
        "postgresql+asyncpg://prompt_etl:prompt_etl@postgres:5432/prompt_etl"
    )

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Object storage
    object_store_kind: str = "s3"  # "s3" | "local"
    s3_endpoint_url: str = "http://minio:9000"
    s3_public_endpoint_url: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "prompt-etl"
    local_store_root: str = "/tmp/prompt-etl-store"

    # Ollama
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_disable: bool = False

    # Engine router
    engine_ram_override_bytes: int | None = None
    engine_spark_threshold_bytes: int = 50 * 1024 * 1024 * 1024  # 50 GiB

    @field_validator("engine_ram_override_bytes", mode="before")
    @classmethod
    def _empty_str_is_none(cls, v):
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    # Worker
    arq_max_jobs: int = 4
    arq_job_timeout_seconds: int = 14400  # 4h

    # Derived helpers
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def is_local(self) -> bool:
        return self.app_env in {"local", "dev", "development"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
