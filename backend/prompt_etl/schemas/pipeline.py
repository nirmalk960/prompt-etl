"""Pipeline DAG schemas."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

NodeKind = Literal[
    "source",
    "filter",
    "select",
    "drop_nulls",
    "fill_nulls",
    "rename",
    "deduplicate",
    "aggregate",
    "sort",
    "custom",
    "write",
]


class PipelineNode(BaseModel):
    id: str = Field(min_length=1)
    kind: NodeKind
    label: str | None = None
    prompt: str | None = None  # natural-language description for LLM
    config: dict[str, Any] = Field(default_factory=dict)
    # `source` nodes carry a dataset_id; `write` nodes carry an output name.
    dataset_id: uuid.UUID | None = None
    output_name: str | None = None


class PipelineEdge(BaseModel):
    source: str
    target: str


class PipelineDefinition(BaseModel):
    nodes: list[PipelineNode]
    edges: list[PipelineEdge]
    # Optional: force a specific engine; otherwise the router decides.
    engine: Literal["auto", "pandas", "polars", "duckdb", "spark"] = "auto"

    @model_validator(mode="after")
    def _validate_ids(self) -> "PipelineDefinition":
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("Duplicate node ids")
        for e in self.edges:
            if e.source not in ids or e.target not in ids:
                raise ValueError(f"Edge references unknown node: {e}")
        return self


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str | None = None
    definition: PipelineDefinition


class WorkflowSummary(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None


class WorkflowDetail(WorkflowSummary):
    definition: PipelineDefinition
