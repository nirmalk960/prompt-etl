"""Datasets API.

Upload flow (presigned multipart):

    1. POST /datasets/init { name, source_format, size_bytes }
       -> { dataset_id, upload_id, parts: [{part_number, url}], object_key }
    2. Client PUTs each part to the URL; collects {part_number, ETag} from
       each response.
    3. POST /datasets/{id}/complete { upload_id, parts: [...] }
       -> enqueues ingest, returns { dataset_id, status, ingest_job_id }
    4. Client polls GET /datasets/{id} until status == "ready" | "failed".

This keeps the API process entirely out of the upload data path.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from typing import Annotated

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy import select

from ..arq_pool import get_arq_pool
from ..db.models import Dataset
from ..config import get_settings
from ..deps import CurrentWorkspace, SessionDep
from ..schemas.dataset import (
    DatasetComplete,
    DatasetCompleteRequest,
    DatasetDetail,
    DatasetInit,
    DatasetInitResponse,
    DatasetPreview,
    DatasetSummary,
    PresignedPartUrl,
)
from ..services.object_store import (
    PresignedMultipartUpload,
    dataset_raw_key,
    get_object_store,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/datasets", tags=["datasets"])


def _sql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _safe_dataset_stem(name: str) -> str:
    stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in name)
    return stem[:120] or "dataset"


@router.post("/init", response_model=DatasetInitResponse, status_code=status.HTTP_201_CREATED)
async def init_upload(
    body: DatasetInit,
    session: SessionDep,
    workspace: CurrentWorkspace,
) -> DatasetInitResponse:
    settings = get_settings()
    if body.size_bytes > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"dataset is larger than the {settings.max_upload_bytes} byte limit",
        )
    store = get_object_store()
    store.ensure_bucket()

    dataset_id = uuid.uuid4()
    object_key = dataset_raw_key(str(workspace.id), str(dataset_id), body.name)

    presigned: PresignedMultipartUpload = store.presign_multipart(
        object_key=object_key,
        size_bytes=body.size_bytes,
        content_type="application/octet-stream",
    )

    ds = Dataset(
        id=dataset_id,
        workspace_id=workspace.id,
        name=body.name,
        source_format=body.source_format,
        raw_object_key=object_key,
        raw_size_bytes=body.size_bytes,
        status="uploading",
    )
    session.add(ds)
    await session.flush()

    return DatasetInitResponse(
        dataset_id=dataset_id,
        upload_id=presigned.upload_id,
        parts=[
            PresignedPartUrl(part_number=p.part_number, url=p.url) for p in presigned.parts
        ],
        object_key=object_key,
        part_size_bytes=presigned.part_size_bytes,
    )


@router.post("/{dataset_id}/complete", response_model=DatasetComplete)
async def complete_upload(
    body: DatasetCompleteRequest,
    session: SessionDep,
    workspace: CurrentWorkspace,
    dataset_id: uuid.UUID = Path(..., description="Dataset ID returned by /init"),
) -> DatasetComplete:
    ds = await session.get(Dataset, dataset_id)
    if ds is None or ds.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="dataset not found")
    if ds.status != "uploading":
        raise HTTPException(status_code=409, detail=f"dataset is in status {ds.status}")

    store = get_object_store()
    try:
        store.complete_multipart(
            object_key=ds.raw_object_key,
            upload_id=body.upload_id,
            parts=[(p.part_number, p.etag) for p in body.parts],
        )
    except Exception as e:
        log.exception("complete_multipart failed for dataset %s", dataset_id)
        ds.status = "failed"
        ds.error = str(e)[:1000]
        raise HTTPException(status_code=400, detail=f"complete_multipart failed: {e}") from e

    head = store.head(ds.raw_object_key)
    if head is None:
        raise HTTPException(status_code=400, detail="object not present after complete")
    ds.raw_size_bytes = int(head.get("ContentLength") or ds.raw_size_bytes)
    ds.status = "ingesting"
    await session.flush()
    await session.commit()

    pool = await get_arq_pool()
    arq_job = await pool.enqueue_job("ingest_dataset", str(dataset_id))
    return DatasetComplete(
        dataset_id=dataset_id,
        status=ds.status,
        ingest_job_id=uuid.UUID(arq_job.job_id) if hasattr(arq_job, "job_id") else None,
    )


@router.get("", response_model=list[DatasetSummary])
async def list_datasets(
    session: SessionDep,
    workspace: CurrentWorkspace,
    limit: int = Query(default=100, le=500),
) -> list[DatasetSummary]:
    rows = await session.execute(
        select(Dataset)
        .where(Dataset.workspace_id == workspace.id)
        .order_by(Dataset.created_at.desc())
        .limit(limit)
    )
    return [
        DatasetSummary(
            id=d.id,
            name=d.name,
            source_format=d.source_format,
            status=d.status,
            raw_size_bytes=d.raw_size_bytes,
            parquet_size_bytes=d.parquet_size_bytes,
            row_count=d.row_count,
            created_at=d.created_at,
            error=d.error,
        )
        for d in rows.scalars()
    ]


@router.get("/{dataset_id}", response_model=DatasetDetail)
async def get_dataset(
    session: SessionDep,
    workspace: CurrentWorkspace,
    dataset_id: uuid.UUID,
) -> DatasetDetail:
    ds = await session.get(Dataset, dataset_id)
    if ds is None or ds.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="dataset not found")
    return DatasetDetail(
        id=ds.id,
        name=ds.name,
        source_format=ds.source_format,
        status=ds.status,
        raw_size_bytes=ds.raw_size_bytes,
        parquet_size_bytes=ds.parquet_size_bytes,
        row_count=ds.row_count,
        created_at=ds.created_at,
        error=ds.error,
        schema_json=ds.schema_json,
        raw_object_key=ds.raw_object_key,
        parquet_object_key=ds.parquet_object_key,
    )


@router.get("/{dataset_id}/preview", response_model=DatasetPreview)
async def preview_dataset(
    session: SessionDep,
    workspace: CurrentWorkspace,
    dataset_id: uuid.UUID,
    n: int = Query(default=50, ge=1, le=500),
) -> DatasetPreview:
    ds = await session.get(Dataset, dataset_id)
    if ds is None or ds.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="dataset not found")
    if ds.parquet_object_key is None:
        raise HTTPException(status_code=409, detail=f"dataset status is {ds.status}")

    # Use DuckDB for a cheap, engine-independent preview.
    from ..engines.duckdb_engine import DuckDBEngine

    preview = await DuckDBEngine().preview(object_key=ds.parquet_object_key, n=n)
    return DatasetPreview(
        columns=preview.columns,
        rows=preview.rows,
        total_rows=ds.row_count,
    )


@router.get("/{dataset_id}/download")
async def download_dataset(
    session: SessionDep,
    workspace: CurrentWorkspace,
    dataset_id: uuid.UUID,
    fmt: str = Query(default="parquet", pattern="^(parquet|csv|json)$"),
) -> dict:
    """Return a presigned URL the client can fetch directly from object storage.

    For CSV/JSON, we currently presign the parquet and rely on the UI to
    download + convert (cheap for typical result sizes). A worker-side
    conversion endpoint can be added later if needed.
    """
    ds = await session.get(Dataset, dataset_id)
    if ds is None or ds.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="dataset not found")
    if ds.parquet_object_key is None:
        raise HTTPException(status_code=409, detail="dataset not ready")
    store = get_object_store()
    url = store.presign_get(ds.parquet_object_key, expires=3600)
    return {"url": url, "format": "parquet", "note": "Phase 1 returns parquet; convert client-side."}


@router.get("/{dataset_id}/export")
async def export_dataset_file(
    session: SessionDep,
    workspace: CurrentWorkspace,
    dataset_id: uuid.UUID,
    fmt: str = Query(default="parquet", pattern="^(parquet|csv|json|txt)$"),
):
    """Export a dataset to a local-downloadable file format.

    - parquet: returns original parquet bytes
    - csv: RFC-ish CSV with header
    - json: line-delimited JSON records
    - txt: tab-delimited flat file with header
    """
    ds = await session.get(Dataset, dataset_id)
    if ds is None or ds.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="dataset not found")
    if ds.parquet_object_key is None:
        raise HTTPException(status_code=409, detail="dataset not ready")

    store = get_object_store()
    tmpdir = tempfile.mkdtemp(prefix="prompt-etl-export-")
    parquet_local = os.path.join(tmpdir, "input.parquet")
    store.download_to_file(ds.parquet_object_key, parquet_local)

    stem = _safe_dataset_stem(ds.name or str(ds.id))
    if fmt == "parquet":
        out_path = os.path.join(tmpdir, f"{stem}.parquet")
        shutil.copy2(parquet_local, out_path)
        media = "application/octet-stream"
    else:
        ext = "json" if fmt == "json" else ("txt" if fmt == "txt" else "csv")
        out_path = os.path.join(tmpdir, f"{stem}.{ext}")
        con = duckdb.connect(database=":memory:")
        try:
            src = _sql_literal(parquet_local)
            dst = _sql_literal(out_path)
            if fmt == "csv":
                con.execute(
                    f"COPY (SELECT * FROM read_parquet({src})) "
                    f"TO {dst} (FORMAT CSV, HEADER, DELIMITER ',')"
                )
                media = "text/csv"
            elif fmt == "json":
                con.execute(
                    f"COPY (SELECT * FROM read_parquet({src})) "
                    f"TO {dst} (FORMAT JSON)"
                )
                media = "application/x-ndjson"
            else:  # txt
                con.execute(
                    f"COPY (SELECT * FROM read_parquet({src})) "
                    f"TO {dst} (FORMAT CSV, HEADER, DELIMITER '\\t')"
                )
                media = "text/plain"
        finally:
            con.close()

    return FileResponse(
        path=out_path,
        media_type=media,
        filename=os.path.basename(out_path),
        background=BackgroundTask(lambda: shutil.rmtree(tmpdir, ignore_errors=True)),
    )
