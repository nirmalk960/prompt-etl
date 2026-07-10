"""Tiered engine selection.

Decision rule (in order):
  1. If user pinned a specific engine in the pipeline definition, honor it.
  2. If input bytes >= ENGINE_SPARK_THRESHOLD_BYTES -> spark (Phase 2; raises in Phase 1).
  3. If a step is pure SQL/aggregate or bytes > RAM/2 -> duckdb (out-of-core capable).
  4. If bytes <= RAM/2 -> polars (lazy + streaming, multi-core).
  5. Otherwise (small data) -> pandas.

These thresholds are intentionally simple. They live in one place so a
single value can be tuned without touching engine code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import get_settings
from .base import DataEngine, EngineKind
from .duckdb_engine import DuckDBEngine
from .pandas_engine import PandasEngine
from .polars_engine import PolarsEngine
from .resources import detect_available_memory_bytes

log = logging.getLogger(__name__)

# Operations DuckDB handles natively well; we prefer it when bytes are large.
_SQL_FRIENDLY = {"filter", "select", "aggregate", "sort", "drop_nulls", "deduplicate"}


@dataclass(slots=True)
class RoutingDecision:
    engine: EngineKind
    reason: str
    detected_ram_bytes: int


class EngineRouter:
    """Picks one engine per pipeline run. Engines themselves are stateless."""

    def __init__(
        self,
        *,
        pandas: PandasEngine | None = None,
        polars: PolarsEngine | None = None,
        duckdb: DuckDBEngine | None = None,
    ):
        settings = get_settings()
        self._pandas = pandas or PandasEngine()
        self._polars = polars or PolarsEngine()
        self._duckdb = duckdb or DuckDBEngine()
        self._ram = detect_available_memory_bytes(settings.engine_ram_override_bytes)
        self._spark_threshold = settings.engine_spark_threshold_bytes
        log.info(
            "engine_router_init ram=%s spark_threshold=%s",
            self._ram,
            self._spark_threshold,
        )

    @property
    def detected_ram_bytes(self) -> int:
        return self._ram

    def get(self, kind: EngineKind) -> DataEngine:
        if kind == "pandas":
            return self._pandas
        if kind == "polars":
            return self._polars
        if kind == "duckdb":
            return self._duckdb
        if kind == "spark":
            raise NotImplementedError(
                "Spark engine is stubbed in Phase 1. See docs/PHASE2_SPARK.md."
            )
        raise ValueError(f"Unknown engine kind: {kind}")

    def decide(
        self,
        *,
        total_input_bytes: int,
        node_kinds: list[str],
        pinned: EngineKind | None = None,
    ) -> RoutingDecision:
        if pinned is not None and pinned != "auto":  # type: ignore[comparison-overlap]
            return RoutingDecision(
                engine=pinned, reason=f"user-pinned ({pinned})", detected_ram_bytes=self._ram
            )

        if total_input_bytes >= self._spark_threshold:
            return RoutingDecision(
                engine="spark",
                reason=(
                    f"input {total_input_bytes:,}B >= spark threshold "
                    f"{self._spark_threshold:,}B"
                ),
                detected_ram_bytes=self._ram,
            )

        ram_half = self._ram // 2
        ram_quarter = self._ram // 4

        sql_only = bool(node_kinds) and all(k in _SQL_FRIENDLY for k in node_kinds)

        if total_input_bytes > ram_half:
            return RoutingDecision(
                engine="duckdb",
                reason=f"input > RAM/2 ({total_input_bytes:,} > {ram_half:,}); out-of-core",
                detected_ram_bytes=self._ram,
            )

        if sql_only and total_input_bytes > ram_quarter:
            return RoutingDecision(
                engine="duckdb",
                reason="all nodes SQL-friendly and data > RAM/4",
                detected_ram_bytes=self._ram,
            )

        if total_input_bytes > ram_quarter:
            return RoutingDecision(
                engine="polars",
                reason=f"medium data fits in RAM/2 ({total_input_bytes:,} <= {ram_half:,})",
                detected_ram_bytes=self._ram,
            )

        return RoutingDecision(
            engine="pandas",
            reason=f"small data ({total_input_bytes:,} <= RAM/4 = {ram_quarter:,})",
            detected_ram_bytes=self._ram,
        )
