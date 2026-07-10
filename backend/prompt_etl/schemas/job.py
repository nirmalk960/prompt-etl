"""Job schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from .pipeline import PipelineDefinition


class JobCreate(BaseModel):
    definition: PipelineDefinition
    workflow_id: uuid.UUID | None = None


class JobSummary(BaseModel):
    id: uuid.UUID
    status: str
    engine: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result_dataset_id: uuid.UUID | None = None


class JobDetail(JobSummary):
    definition: PipelineDefinition
    events: list[dict[str, Any]] = []
