"""Dataset request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SupportedFormat = Literal["csv", "json", "xlsx", "parquet"]


class DatasetInit(BaseModel):
    """Body for POST /datasets/init - request a presigned upload."""

    name: str = Field(min_length=1, max_length=300)
    source_format: SupportedFormat
    size_bytes: int = Field(ge=0)


class PresignedPartUrl(BaseModel):
    part_number: int
    url: str


class DatasetInitResponse(BaseModel):
    dataset_id: uuid.UUID
    upload_id: str
    parts: list[PresignedPartUrl]
    object_key: str
    part_size_bytes: int


class CompletedPart(BaseModel):
    part_number: int
    etag: str


class DatasetCompleteRequest(BaseModel):
    upload_id: str
    parts: list[CompletedPart]


class DatasetComplete(BaseModel):
    """Returned once the multipart upload is acknowledged. Ingest is queued."""

    dataset_id: uuid.UUID
    status: str
    ingest_job_id: uuid.UUID | None = None


class DatasetSummary(BaseModel):
    id: uuid.UUID
    name: str
    source_format: str
    status: str
    raw_size_bytes: int
    parquet_size_bytes: int | None = None
    row_count: int | None = None
    created_at: datetime
    error: str | None = None


class DatasetDetail(DatasetSummary):
    schema_json: dict[str, Any] | None = None
    raw_object_key: str
    parquet_object_key: str | None = None


class DatasetPreview(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    total_rows: int | None = None
