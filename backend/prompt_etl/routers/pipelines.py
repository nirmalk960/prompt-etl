"""Pipeline helpers that don't run anything (preflight)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..deps import CurrentWorkspace, SessionDep
from ..schemas.pipeline import PipelineDefinition
from ..services.schema_preview import preview_schemas

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


@router.post("/schema")
async def pipeline_schema(
    body: PipelineDefinition, session: SessionDep, workspace: CurrentWorkspace
) -> dict[str, Any]:
    return {"nodes": await preview_schemas(session, workspace.id, body)}
