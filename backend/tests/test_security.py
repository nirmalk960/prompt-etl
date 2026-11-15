"""Regression checks for the public-deployment boundaries."""

from __future__ import annotations

import asyncio
import os
import tempfile

import pandas as pd
import pytest

from prompt_etl.config import Settings
from prompt_etl.deps import authorized_email
from prompt_etl.engines.duckdb_engine import DuckDBEngine
from prompt_etl.engines.pandas_engine import PandasEngine
from prompt_etl.engines.polars_engine import PolarsEngine
from prompt_etl.services.agent_guard import validate_python, validate_sql
from prompt_etl.services.object_store import LocalObjectStore, get_object_store
from prompt_etl.services.operation_templates import stub_codegen


@pytest.fixture
def tiny_key() -> str:
    frame = pd.DataFrame({"department": ["Engineering"], "salary": [1]})
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "tiny.parquet")
        frame.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
        store = get_object_store()
        store.ensure_bucket()
        key = "test/security-tiny.parquet"
        store.upload_file(path, key)
        return key


def test_dev_token_is_rejected_unless_enabled() -> None:
    locked = Settings(allow_dev_auth=False, access_token="s3cret-token", operator_email="you@local")
    assert authorized_email("dev:you@local", locked) is None
    assert authorized_email("dev:other@local", locked) is None
    assert authorized_email("s3cret-token", locked) == "you@local"
    assert authorized_email("wrong-token", locked) is None
    assert authorized_email("", locked) is None


def test_dev_token_works_only_when_the_switch_is_on() -> None:
    opened = Settings(allow_dev_auth=True, access_token="", operator_email="you@local")
    assert authorized_email("dev:you@local", opened) == "you@local"
    assert authorized_email("dev:not-an-email", opened) is None


def test_object_key_cannot_escape_the_bucket() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalObjectStore(root=tmp, bucket="b")
        store.ensure_bucket()
        for key in ("../secret", "a/../../secret", "/etc/passwd", "a//b", "a/./b"):
            with pytest.raises(ValueError):
                store._path_for(key)
        path = store._path_for("workspaces/one/data.parquet")
        assert path.startswith(os.path.abspath(store._bucket_root()) + os.sep)


def test_python_guard_rejects_module_and_file_access() -> None:
    assert validate_python("result = pd.io.common.os.listdir('/')") is not None
    assert validate_python("result = pd.read_csv('/etc/passwd')") is not None
    assert validate_python("result = pl.read_csv('/etc/passwd')") is not None
    assert validate_python("result = df.eval('1')") is not None
    assert validate_python("result = df[df['department'] == 'Engineering']") is None


def test_sql_guard_rejects_file_reads_and_comments() -> None:
    assert validate_sql("SELECT * FROM read_csv_auto('/tmp/x')") is not None
    assert validate_sql("SELECT 1 FROM read_csv_auto('/tmp/x') t --") is not None
    assert validate_sql("SELECT * FROM df WHERE region = 'East'") is None


def test_aggregate_stub_does_not_splice_sql(employees_schema: list[dict[str, str]]) -> None:
    sql = stub_codegen(
        engine="duckdb",
        node_kind="aggregate",
        user_prompt="group by 1 FROM read_csv_auto('/tmp/x') t -- calculate total (sum order_total)",
        schema=employees_schema,
    )
    assert sql is None or validate_sql(sql) is not None


@pytest.fixture
def employees_schema() -> list[dict[str, str]]:
    return [
        {"name": "order_total", "dtype": "float64"},
        {"name": "region", "dtype": "object"},
    ]


def test_pandas_engine_refuses_os(tiny_key: str) -> None:
    engine = PandasEngine()
    with pytest.raises(RuntimeError):
        asyncio.run(
            engine.execute_step(
                node_id="t",
                node_kind="custom",
                code="result = pd.io.common.os.listdir('/')",
                input_object_keys=[tiny_key],
                output_object_key="test/blocked.parquet",
            )
        )


def test_polars_engine_refuses_read_csv(tiny_key: str) -> None:
    engine = PolarsEngine()
    with pytest.raises(RuntimeError):
        asyncio.run(
            engine.execute_step(
                node_id="t",
                node_kind="custom",
                code="result = pl.read_csv('/etc/passwd')",
                input_object_keys=[tiny_key],
                output_object_key="test/blocked-pl.parquet",
            )
        )


def test_duckdb_engine_refuses_read_csv(tiny_key: str) -> None:
    engine = DuckDBEngine()
    with pytest.raises(RuntimeError):
        asyncio.run(
            engine.execute_step(
                node_id="t",
                node_kind="custom",
                code="SELECT * FROM read_csv_auto('/etc/passwd')",
                input_object_keys=[tiny_key],
                output_object_key="test/blocked-sql.parquet",
            )
        )
