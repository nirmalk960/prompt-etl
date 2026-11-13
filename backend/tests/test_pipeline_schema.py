"""Pipeline definition rules for configured operations."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prompt_etl.schemas.pipeline import PipelineDefinition, PipelineNode
from prompt_etl.services.pipeline import parents_of


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown operation"):
        PipelineNode(id="a", kind="explode_everything")


def test_effective_mode() -> None:
    assert PipelineNode(id="a", kind="filter", prompt="x").effective_mode == "prompt"
    assert PipelineNode(id="a", kind="filter", config={"conditions": []}).effective_mode == "configure"
    assert PipelineNode(id="a", kind="filter", mode="prompt", config={"x": 1}).effective_mode == "prompt"
    # Python ops have no prompt mode; custom has no configure mode.
    assert PipelineNode(id="a", kind="cluster", mode="prompt").effective_mode == "configure"
    assert PipelineNode(id="a", kind="custom", mode="configure").effective_mode == "prompt"


def test_parents_left_before_right() -> None:
    d = PipelineDefinition.model_validate({
        "nodes": [{"id": "r", "kind": "source"}, {"id": "l", "kind": "source"},
                  {"id": "j", "kind": "join"}],
        "edges": [{"source": "r", "target": "j", "target_handle": "right"},
                  {"source": "l", "target": "j", "target_handle": "left"}],
    })
    assert parents_of(d)["j"] == ["l", "r"]
