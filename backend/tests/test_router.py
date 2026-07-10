from __future__ import annotations

from prompt_etl.engines.router import EngineRouter


def test_router_picks_pandas_for_tiny_data() -> None:
    router = EngineRouter()
    decision = router.decide(
        total_input_bytes=10 * 1024 * 1024,  # 10 MiB
        node_kinds=["filter", "select"],
        pinned=None,
    )
    assert decision.engine == "pandas"


def test_router_picks_polars_for_medium_data() -> None:
    router = EngineRouter()
    ram = router.detected_ram_bytes
    decision = router.decide(
        total_input_bytes=ram // 3,  # > RAM/4, <= RAM/2 -> polars
        node_kinds=["custom"],
        pinned=None,
    )
    assert decision.engine == "polars"


def test_router_picks_duckdb_for_large_or_sql_data() -> None:
    router = EngineRouter()
    ram = router.detected_ram_bytes
    decision = router.decide(
        total_input_bytes=int(ram * 0.7),  # > RAM/2 -> duckdb (out-of-core)
        node_kinds=["filter"],
        pinned=None,
    )
    assert decision.engine == "duckdb"


def test_router_honors_pin() -> None:
    router = EngineRouter()
    decision = router.decide(
        total_input_bytes=1024,
        node_kinds=["filter"],
        pinned="duckdb",
    )
    assert decision.engine == "duckdb"


def test_router_routes_to_spark_above_threshold() -> None:
    router = EngineRouter()
    decision = router.decide(
        total_input_bytes=router._spark_threshold + 1,  # type: ignore[attr-defined]
        node_kinds=["custom"],
        pinned=None,
    )
    assert decision.engine == "spark"
