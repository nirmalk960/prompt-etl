"""Engine ABC.

Every concrete engine works on Parquet files in object storage and writes
intermediate step outputs as Parquet too (the "step checkpoints"). This keeps
engines interchangeable: a Polars step can hand off to a DuckDB step in the
next pipeline run without conversion code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

EngineKind = Literal["pandas", "polars", "duckdb", "spark"]


@dataclass(slots=True)
class StepPreview:
    """A small head sample of a step's output, returned to the UI."""

    columns: list[str]
    rows: list[list[Any]]
    row_count: int | None = None
    bytes: int | None = None
    schema: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class StepResult:
    """Result of running one node.

    `output_object_key` is the parquet checkpoint path in object storage.
    """

    output_object_key: str
    row_count: int
    byte_size: int
    preview: StepPreview


class DataEngine(ABC):
    """Engine interface used by the pipeline executor."""

    kind: EngineKind

    @abstractmethod
    async def execute_step(
        self,
        *,
        node_id: str,
        node_kind: str,
        code: str,
        input_object_keys: list[str],
        output_object_key: str,
        config: dict[str, Any] | None = None,
    ) -> StepResult:
        """Execute one node's generated code against the inputs and write the
        result to ``output_object_key`` as Parquet.

        ``code`` is engine-specific (pandas/Polars Python, or DuckDB SQL).
        Implementations MUST NOT mutate inputs and MUST produce a deterministic
        output keyed by ``output_object_key``.
        """

    @abstractmethod
    async def preview(self, *, object_key: str, n: int = 50) -> StepPreview:
        """Return a small head sample of a parquet object for UI display."""
