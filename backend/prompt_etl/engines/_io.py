"""Shared engine I/O helpers: download inputs, upload outputs, build previews."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from typing import Iterator

from ..services.object_store import get_object_store


@contextmanager
def staged_inputs(object_keys: list[str]) -> Iterator[list[str]]:
    """Download each object_key to a temp file and yield the local paths.

    Uses a single TemporaryDirectory so all temp files are cleaned up together.
    Files are streamed to disk to support objects larger than memory.
    """
    store = get_object_store()
    with tempfile.TemporaryDirectory(prefix="prompt-etl-step-") as tmp:
        local_paths: list[str] = []
        for i, key in enumerate(object_keys):
            local = os.path.join(tmp, f"input_{i}.parquet")
            store.download_to_file(key, local)
            local_paths.append(local)
        yield local_paths


@contextmanager
def staged_output() -> Iterator[str]:
    """Yield a local path the engine can write its parquet to. Caller uploads."""
    with tempfile.TemporaryDirectory(prefix="prompt-etl-out-") as tmp:
        yield os.path.join(tmp, "out.parquet")


def upload_output(local_path: str, object_key: str) -> tuple[int, int]:
    """Upload local parquet and return (byte_size, row_count_approx).

    Row count is computed from parquet metadata for cheapness — exact and
    cluster-friendly. Engines may override with a better row count if they
    already have it.
    """
    import pyarrow.parquet as pq

    store = get_object_store()
    size = os.path.getsize(local_path)
    store.upload_file(local_path, object_key, content_type="application/octet-stream")
    meta = pq.read_metadata(local_path)
    return size, meta.num_rows
