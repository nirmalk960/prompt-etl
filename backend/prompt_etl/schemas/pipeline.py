"""Pipeline DAG schemas."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

IO_KINDS = {"source", "write"}


class PipelineNode(BaseModel):
    id: str = Field(min_length=1)
    # "source", "write", or an operation id from GET /operations.
    kind: str
    label: str | None = None
    prompt: str | None = None  # natural-language description for LLM
    # "configure" runs the op from `config` without an LLM; "prompt" generates
    # code from `prompt`. Unset = configure when there is a config.
    mode: Literal["configure", "prompt"] | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    # `source` nodes carry a dataset_id; `write` nodes carry an output name.
    dataset_id: uuid.UUID | None = None
    output_name: str | None = None

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        from ..ops import get_op

        if v not in IO_KINDS and get_op(v) is None:
            raise ValueError(f"unknown operation '{v}'")
        return v

    @property
    def effective_mode(self) -> Literal["configure", "prompt"]:
        from ..ops import get_op

        spec = get_op(self.kind)
        if spec is not None and not spec.supports_prompt:
            return "configure"
        if spec is not None and not spec.supports_configure:
            return "prompt"
        if self.mode is not None:
            return self.mode
        return "configure" if self.config else "prompt"


class PipelineEdge(BaseModel):
    source: str
    target: str
    # Two-input operations: "left" (input 0) or "right" (input 1).
    target_handle: str | None = None


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
