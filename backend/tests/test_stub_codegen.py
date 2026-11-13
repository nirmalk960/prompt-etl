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


_ORDERS_SCHEMA = [
    {"name": "order_total", "dtype": "float64"},
    {"name": "region", "dtype": "object"},
]


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Filter rows where order_total is greater than 50", "result = df[df['order_total'] > 50]"),
        ("Filter rows where order_total greater than 50", "result = df[df['order_total'] > 50]"),
        ("Filter rows where order_total > 50", "result = df[df['order_total'] > 50]"),
        ("Keep rows where order_total is at least 50", "result = df[df['order_total'] >= 50]"),
        ("Keep only rows where order_total is less than or equal to 20.5", "result = df[df['order_total'] <= 20.5]"),
        ("Filter rows where order_total is below 30", "result = df[df['order_total'] < 30]"),
        ("Filter rows where region is North America", "result = df[df['region'] == 'North America']"),
        ("Filter rows where region equals 'EU'", "result = df[df['region'] == 'EU']"),
        ("Filter rows where region is not EU", "result = df[df['region'] != 'EU']"),
        ("Filter rows where region not equals EU", "result = df[df['region'] != 'EU']"),
    ],
)
def test_filter_phrasings(prompt: str, expected: str) -> None:
    assert stub_codegen(engine="pandas", node_kind="filter", user_prompt=prompt, schema=_ORDERS_SCHEMA) == expected


@pytest.mark.parametrize(
    "prompt",
    [
        "Filter rows where order_total is high",
        "Filter rows where region is in North, South",
        "Filter rows where region is null",
        "Filter rows where missing_col equals 5",
    ],
)
def test_filter_defers_to_llm(prompt: str) -> None:
    assert stub_codegen(engine="pandas", node_kind="filter", user_prompt=prompt, schema=_ORDERS_SCHEMA) is None


def test_unknown_returns_none() -> None:
    assert (
        stub_codegen(engine="pandas", node_kind="filter", user_prompt="please be magical")
        is None
    )


def test_duckdb_rejects_drop() -> None:
    assert validate("duckdb", "DROP TABLE df") is not None


def test_pandas_rejects_open() -> None:
    assert validate("pandas", "result = open('/etc/passwd').read()") is not None
