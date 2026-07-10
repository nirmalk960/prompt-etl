"""Workflows (saved pipeline definitions) CRUD."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..db.models import Workflow
from ..deps import CurrentWorkspace, SessionDep
from ..schemas.pipeline import (
    PipelineDefinition,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowSummary,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post("", response_model=WorkflowDetail, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    body: WorkflowCreate,
    session: SessionDep,
    workspace: CurrentWorkspace,
) -> WorkflowDetail:
    wf = Workflow(
        workspace_id=workspace.id,
        name=body.name,
        description=body.description,
        definition=body.definition.model_dump(mode="json"),
    )
    session.add(wf)
    await session.flush()
    return _to_detail(wf)


@router.get("", response_model=list[WorkflowSummary])
async def list_workflows(
    session: SessionDep, workspace: CurrentWorkspace
) -> list[WorkflowSummary]:
    rows = await session.execute(
        select(Workflow)
        .where(Workflow.workspace_id == workspace.id)
        .order_by(Workflow.created_at.desc())
    )
    return [
        WorkflowSummary(id=w.id, name=w.name, description=w.description)
        for w in rows.scalars()
    ]


@router.get("/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow(
    session: SessionDep, workspace: CurrentWorkspace, workflow_id: uuid.UUID
) -> WorkflowDetail:
    wf = await session.get(Workflow, workflow_id)
    if wf is None or wf.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="workflow not found")
    return _to_detail(wf)


@router.put("/{workflow_id}", response_model=WorkflowDetail)
async def update_workflow(
    body: WorkflowCreate,
    session: SessionDep,
    workspace: CurrentWorkspace,
    workflow_id: uuid.UUID,
) -> WorkflowDetail:
    wf = await session.get(Workflow, workflow_id)
    if wf is None or wf.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="workflow not found")
    wf.name = body.name
    wf.description = body.description
    wf.definition = body.definition.model_dump(mode="json")
    await session.flush()
    return _to_detail(wf)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    session: SessionDep, workspace: CurrentWorkspace, workflow_id: uuid.UUID
) -> None:
    wf = await session.get(Workflow, workflow_id)
    if wf is None or wf.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="workflow not found")
    await session.delete(wf)


def _to_detail(wf: Workflow) -> WorkflowDetail:
    return WorkflowDetail(
        id=wf.id,
        name=wf.name,
        description=wf.description,
        definition=PipelineDefinition.model_validate(wf.definition),
    )
