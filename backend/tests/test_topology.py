from __future__ import annotations

import pytest

from prompt_etl.schemas.pipeline import PipelineDefinition
from prompt_etl.services.pipeline import PipelineError, topological_order


def _pd(nodes: list[dict], edges: list[dict]) -> PipelineDefinition:
    return PipelineDefinition.model_validate({"nodes": nodes, "edges": edges})


def test_linear_topo() -> None:
    d = _pd(
        [
            {"id": "s", "kind": "source"},
            {"id": "f", "kind": "filter", "prompt": "Filter rows where x equals 1"},
            {"id": "w", "kind": "write"},
        ],
        [{"source": "s", "target": "f"}, {"source": "f", "target": "w"}],
    )
    assert topological_order(d) == ["s", "f", "w"]


def test_cycle_raises() -> None:
    d = _pd(
        [
            {"id": "a", "kind": "custom", "prompt": "passthrough"},
            {"id": "b", "kind": "custom", "prompt": "passthrough"},
        ],
        [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
    )
    with pytest.raises(PipelineError):
        topological_order(d)
