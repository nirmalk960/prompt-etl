"""Raw-file -> Parquet ingest using DuckDB streaming.

DuckDB is excellent at this: a single COPY/SELECT can stream a 30 GB CSV
into Parquet without ever materializing it in memory. We download the raw
file to a temp path (boto3 streams to disk), run DuckDB locally against it,
and upload the parquet output.

For XLSX (which DuckDB can't read natively without extensions), we fall back
to pandas with openpyxl since spreadsheets are practically capped at ~1M rows.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Any

import duckdb
import pyarrow.parquet as pq

from .object_store import get_object_store

log = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestResult:
    parquet_object_key: str
    parquet_size_bytes: int
    row_count: int
    schema: list[dict[str, str]]


def ingest_to_parquet(
    *,
    raw_object_key: str,
    parquet_object_key: str,
    source_format: str,
) -> IngestResult:
    store = get_object_store()
    with tempfile.TemporaryDirectory(prefix="prompt-etl-ingest-") as tmp:
        raw_local = os.path.join(tmp, "raw")
        parquet_local = os.path.join(tmp, "out.parquet")

        log.info("ingest: downloading %s", raw_object_key)
        store.download_to_file(raw_object_key, raw_local)

        fmt = source_format.lower()
        if fmt == "csv":
            _duckdb_copy(
                f"SELECT * FROM read_csv_auto('{raw_local}', SAMPLE_SIZE=-1)",
                parquet_local,
            )
        elif fmt == "json":
            _duckdb_copy(
                f"SELECT * FROM read_json_auto('{raw_local}')",
                parquet_local,
            )
        elif fmt == "parquet":
            # Already parquet: just re-encode with zstd for consistency.
            _duckdb_copy(
                f"SELECT * FROM read_parquet('{raw_local}')",
                parquet_local,
            )
        elif fmt in {"xlsx", "xls"}:
            _xlsx_to_parquet(raw_local, parquet_local)
        else:
            raise ValueError(f"Unsupported source format: {source_format}")

        meta = pq.read_metadata(parquet_local)
        schema = [
            {"name": f.name, "dtype": str(f.physical_type)} for f in meta.schema
        ]
        size = os.path.getsize(parquet_local)

        log.info(
            "ingest: uploading parquet %s rows=%s bytes=%s",
            parquet_object_key,
            meta.num_rows,
            size,
        )
        store.upload_file(parquet_local, parquet_object_key, content_type="application/octet-stream")

        return IngestResult(
            parquet_object_key=parquet_object_key,
            parquet_size_bytes=size,
            row_count=meta.num_rows,
            schema=schema,
        )


def _duckdb_copy(select_sql: str, parquet_local: str) -> None:
    con = duckdb.connect()
    try:
        # Use a single thread for predictable memory in tight workers; bump for big nodes.
        con.execute("PRAGMA threads=4")
        con.execute(
            f"COPY ({select_sql}) TO '{parquet_local}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"
        )
    finally:
        con.close()


def _xlsx_to_parquet(xlsx_path: str, parquet_local: str) -> None:
    import pandas as pd

    df = pd.read_excel(xlsx_path, engine="openpyxl")
    df.to_parquet(parquet_local, engine="pyarrow", compression="zstd", index=False)


def schema_summary(schema: list[dict[str, str]]) -> dict[str, Any]:
    return {"columns": [c["name"] for c in schema], "fields": schema}
