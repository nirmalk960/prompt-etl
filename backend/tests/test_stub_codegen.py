from __future__ import annotations

import pytest

from prompt_etl.services.agent_guard import validate
from prompt_etl.services.operation_templates import stub_codegen


@pytest.mark.parametrize("engine", ["pandas", "polars", "duckdb"])
def test_filter_eq(engine: str) -> None:
    code = stub_codegen(
        engine=engine,  # type: ignore[arg-type]
        node_kind="filter",
        user_prompt="Filter rows where department equals Engineering",
    )
    assert code is not None
    assert validate(engine, code) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("engine", ["pandas", "polars", "duckdb"])
def test_aggregate(engine: str) -> None:
    code = stub_codegen(
        engine=engine,  # type: ignore[arg-type]
        node_kind="aggregate",
        user_prompt="Sum of salary by department",
    )
    assert code is not None
    assert validate(engine, code) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("engine", ["pandas", "polars", "duckdb"])
def test_select(engine: str) -> None:
    code = stub_codegen(
        engine=engine,  # type: ignore[arg-type]
        node_kind="select",
        user_prompt="Keep columns id, name, salary",
    )
    assert code is not None
    assert validate(engine, code) is None  # type: ignore[arg-type]


def test_unknown_returns_none() -> None:
    assert (
        stub_codegen(engine="pandas", node_kind="filter", user_prompt="please be magical")
        is None
    )


def test_duckdb_rejects_drop() -> None:
    assert validate("duckdb", "DROP TABLE df") is not None


def test_pandas_rejects_open() -> None:
    assert validate("pandas", "result = open('/etc/passwd').read()") is not None
