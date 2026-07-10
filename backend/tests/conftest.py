"""Shared pytest fixtures.

Tests run against the LocalObjectStore (no MinIO required) and do not need
Postgres/Redis to exercise engine + codegen logic.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest

os.environ.setdefault("OBJECT_STORE_KIND", "local")
os.environ.setdefault("OLLAMA_DISABLE", "1")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("ENGINE_RAM_OVERRIDE_BYTES", str(4 * 1024 * 1024 * 1024))

from prompt_etl.config import get_settings  # noqa: E402
from prompt_etl.services.object_store import reset_object_store_cache  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _store_root() -> Iterator[str]:
    with tempfile.TemporaryDirectory(prefix="prompt-etl-test-store-") as d:
        os.environ["LOCAL_STORE_ROOT"] = d
        get_settings.cache_clear()
        reset_object_store_cache()
        yield d
