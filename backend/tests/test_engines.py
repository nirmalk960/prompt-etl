"""Smoke tests for the three single-node engines on a tiny dataset."""

from __future__ import annotations

import asyncio
import os
import tempfile

import pandas as pd
import pytest

from prompt_etl.engines.duckdb_engine import DuckDBEngine
from prompt_etl.engines.pandas_engine import PandasEngine
from prompt_etl.engines.polars_engine import PolarsEngine
from prompt_etl.services.object_store import get_object_store


@pytest.fixture
def employees_parquet_key() -> str:
    df = pd.DataFrame(
        [
            {"id": 1, "name": "Alice", "department": "Engineering", "salary": 95000},
            {"id": 2, "name": "Bob", "department": "Sales", "salary": 70000},
            {"id": 3, "name": "Carol", "department": "Engineering", "salary": 105000},
            {"id": 4, "name": "Dan", "department": "Marketing", "salary": 80000},
            {"id": 5, "name": "Eve", "department": "Engineering", "salary": 120000},
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "emp.parquet")
        df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
        store = get_object_store()
        store.ensure_bucket()
        key = "test/employees.parquet"
        store.upload_file(path, key)
        return key


def test_pandas_filter(employees_parquet_key: str) -> None:
    engine = PandasEngine()
    result = asyncio.run(
        engine.execute_step(
            node_id="t1",
            node_kind="filter",
            code="result = df[df['department'] == 'Engineering']",
            input_object_keys=[employees_parquet_key],
            output_object_key="test/out_pandas_filter.parquet",
        )
    )
    assert result.row_count == 3
    assert "department" in result.preview.columns


def test_polars_filter(employees_parquet_key: str) -> None:
    engine = PolarsEngine()
    result = asyncio.run(
        engine.execute_step(
            node_id="t1",
            node_kind="filter",
            code="result = df.filter(pl.col('department') == 'Engineering')",
            input_object_keys=[employees_parquet_key],
            output_object_key="test/out_polars_filter.parquet",
        )
    )
    assert result.row_count == 3


def test_duckdb_filter(employees_parquet_key: str) -> None:
    engine = DuckDBEngine()
    result = asyncio.run(
        engine.execute_step(
            node_id="t1",
            node_kind="filter",
            code="SELECT * FROM df WHERE department = 'Engineering'",
            input_object_keys=[employees_parquet_key],
            output_object_key="test/out_duckdb_filter.parquet",
        )
    )
    assert result.row_count == 3


def test_duckdb_aggregate(employees_parquet_key: str) -> None:
    engine = DuckDBEngine()
    result = asyncio.run(
        engine.execute_step(
            node_id="t1",
            node_kind="aggregate",
            code="SELECT department, SUM(salary) AS total FROM df GROUP BY department",
            input_object_keys=[employees_parquet_key],
            output_object_key="test/out_duckdb_agg.parquet",
        )
    )
    assert result.row_count == 3
    assert "total" in result.preview.columns


def test_pandas_rejects_import(employees_parquet_key: str) -> None:
    """Sanity: even though the engine runs in a restricted namespace, the
    agent_guard should reject imports before exec() is called."""
    from prompt_etl.services.agent_guard import validate_python

    err = validate_python("import os\nresult = df")
    assert err is not None and "import" in err
