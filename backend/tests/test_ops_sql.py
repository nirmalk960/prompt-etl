"""Every SQL op: compile, execute in the sandbox, check values; plus dry-run schemas."""

from __future__ import annotations

import os
import tempfile
from typing import Any

import duckdb
import pytest

from prompt_etl.ops import OpError, get_op
from prompt_etl.ops.runner import _run_sql_sync, dry_run_schema
from prompt_etl.ops.sqlsafe import safe_expr

ORDERS = """
SELECT * FROM (VALUES
  (1, 'alice', 'North', DATE '2025-01-05', 120.0, 10.0,  'delivered', 'AB-12'),
  (2, 'bob',   'South', DATE '2025-01-20',  40.0, NULL,  'pending',   'CD-7'),
  (3, 'carol', 'North', DATE '2025-02-11',  75.5, 5.0,   'shipped',   'EF-301'),
  (4, 'dave',  'East',  DATE '2025-02-28', 300.0, NULL,  'delivered', 'GH-4'),
  (5, 'erin',  'South', DATE '2025-03-03',  50.0, 2.5,   'cancelled', 'IJ-55'),
  (6, 'alice', 'North', DATE '2025-03-15', 220.0, 20.0,  'delivered', 'KL-6'),
  (6, 'alice', 'North', DATE '2025-03-15', 220.0, 20.0,  'delivered', 'KL-6'),
  (7, '  frank  smith ', NULL, DATE '2025-04-01', 10.0, NULL, 'pending', 'MN-8')
) t(order_id, customer, region, order_date, order_total, discount, status, code)
"""
REGIONS = """
SELECT * FROM (VALUES ('North', 'Nora'), ('South', 'Sam'), ('West', 'Will'))
t(region, manager)
"""


def _write(sql: str, path: str) -> None:
    con = duckdb.connect()
    con.execute(f"COPY ({sql}) TO '{path}' (FORMAT PARQUET)")
    con.close()


def run(op_id: str, cfg: dict[str, Any], *, right: bool = False) -> list[dict[str, Any]]:
    spec = get_op(op_id)
    assert spec is not None, op_id
    with tempfile.TemporaryDirectory() as tin, tempfile.TemporaryDirectory() as tout:
        paths = [os.path.join(tin, "in0.parquet")]
        _write(ORDERS, paths[0])
        if right:
            paths.append(os.path.join(tin, "in1.parquet"))
            _write(REGIONS, paths[1])
        out = os.path.join(tout, "out.parquet")
        _run_sql_sync(spec, cfg, paths, out)
        con = duckdb.connect()
        cur = con.execute(f"SELECT * FROM read_parquet('{out}')")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        con.close()
        return rows


def ids(rows: list[dict[str, Any]]) -> list[int]:
    return [r["order_id"] for r in rows]


# --- columns ---------------------------------------------------------------

def test_select_and_drop() -> None:
    assert list(run("select", {"columns": ["region", "order_id"]})[0]) == ["region", "order_id"]
    assert "discount" not in run("drop_columns", {"columns": ["discount"]})[0]


def test_rename_and_clash() -> None:
    assert "area" in run("rename", {"renames": [{"from": "region", "to": "area"}]})[0]
    with pytest.raises(OpError, match="already exists"):
        run("rename", {"renames": [{"from": "region", "to": "status"}]})


def test_cast() -> None:
    rows = run("cast", {"casts": [{"column": "order_total", "to": "integer"}, {"column": "order_id", "to": "text"}]})
    assert rows[0]["order_total"] == 120 and rows[0]["order_id"] == "1"


def test_formula_and_safety() -> None:
    rows = run("formula", {"new_column": "net", "expression": "order_total - coalesce(discount, 0)"})
    assert rows[0]["net"] == 110.0 and rows[1]["net"] == 40.0
    for bad in ["(select 1)", "read_csv('/etc/passwd')", "1; drop table x", "getenv('HOME')"]:
        with pytest.raises(OpError):
            safe_expr(bad, [("a", "INTEGER")])
    assert safe_expr("extract(year from order_date)", [("order_date", "DATE")])


def test_conditional_column() -> None:
    rows = run("conditional_column", {
        "new_column": "size",
        "rules": [{"column": "order_total", "operator": ">=", "value": "200", "then": "large"},
                  {"column": "order_total", "operator": ">=", "value": "50", "then": "medium"}],
        "else_value": "small",
    })
    assert [r["size"] for r in rows[:5]] == ["medium", "small", "medium", "large", "medium"]


def test_concat() -> None:
    rows = run("concat_columns", {"columns": ["region", "status"], "separator": "/", "new_column": "k"})
    assert rows[0]["k"] == "North/delivered"


# --- rows ------------------------------------------------------------------

def test_filter_operators() -> None:
    assert ids(run("filter", {"conditions": [{"column": "order_total", "operator": ">", "value": "100"}]})) == [1, 4, 6, 6]
    assert ids(run("filter", {"conditions": [{"column": "region", "operator": "in", "value": "East, South"}]})) == [2, 4, 5]
    assert ids(run("filter", {"conditions": [{"column": "discount", "operator": "is_null"}]})) == [2, 4, 7]
    assert ids(run("filter", {"conditions": [{"column": "order_total", "operator": "between", "value": "40", "value2": "75.5"}]})) == [2, 3, 5]
    assert ids(run("filter", {"conditions": [{"column": "code", "operator": "regex", "value": "^[A-Z]{2}-\\d{3}$"}]})) == [3]
    assert ids(run("filter", {"conditions": [{"column": "status", "operator": "contains", "value": "DELIV"}]})) == [1, 4, 6, 6]
    any_rows = run("filter", {"match": "any", "conditions": [
        {"column": "region", "operator": "=", "value": "East"},
        {"column": "order_total", "operator": "<", "value": "20"}]})
    assert ids(any_rows) == [4, 7]
    dates = run("filter", {"conditions": [{"column": "order_date", "operator": ">=", "value": "2025-03-01"}]})
    assert ids(dates) == [5, 6, 6, 7]


def test_filter_rejects_text_on_number() -> None:
    with pytest.raises(OpError, match="not a number"):
        run("filter", {"conditions": [{"column": "order_total", "operator": "=", "value": "greater"}]})


def test_drop_nulls() -> None:
    assert ids(run("drop_nulls", {})) == [1, 3, 5, 6, 6]
    assert ids(run("drop_nulls", {"columns": ["region"]})) == [1, 2, 3, 4, 5, 6, 6]


def test_fill_nulls_methods() -> None:
    rows = run("fill_nulls", {"fills": [{"column": "discount", "method": "value", "value": "0"}]})
    assert [r["discount"] for r in rows] == [10.0, 0.0, 5.0, 0.0, 2.5, 20.0, 20.0, 0.0]
    rows = run("fill_nulls", {"fills": [{"column": "discount", "method": "previous"}]})
    assert [r["discount"] for r in rows] == [10.0, 10.0, 5.0, 5.0, 2.5, 20.0, 20.0, 20.0]
    rows = run("fill_nulls", {"fills": [{"column": "discount", "method": "median"}]})
    assert rows[1]["discount"] == 10.0
    rows = run("fill_nulls", {"fills": [{"column": "region", "method": "mode"}]})
    assert rows[-1]["region"] == "North"
    with pytest.raises(OpError, match="not numeric"):
        run("fill_nulls", {"fills": [{"column": "region", "method": "mean"}]})


def test_dedupe() -> None:
    assert len(run("deduplicate", {})) == 7
    rows = run("deduplicate", {"columns": ["customer"], "keep": "last"})
    assert {r["customer"]: r["order_id"] for r in rows}["alice"] == 6
    assert len(rows[0]) == 8


def test_sort_top_sample() -> None:
    rows = run("sort", {"sorts": [{"column": "order_total", "direction": "desc"}]})
    assert ids(rows)[:2] == [4, 6]
    assert ids(run("top_n", {"count": 2, "order_by": "order_total"})) == [4, 6]
    assert ids(run("top_n", {"count": 3})) == [1, 2, 3]
    assert len(run("sample", {"method": "rows", "size": 3, "seed": 1})) == 3


# --- text / dates ----------------------------------------------------------

def test_text_ops() -> None:
    rows = run("clean_text", {"columns": ["customer"], "case": "title"})
    assert rows[-1]["customer"] == "Frank Smith"
    rows = run("replace_text", {"column": "status", "find": "deliv", "replace_with": "D", "case_sensitive": False})
    assert rows[0]["status"] == "Dered"
    rows = run("split_column", {"column": "code", "delimiter": "-", "parts": 2})
    assert (rows[0]["code_1"], rows[0]["code_2"]) == ("AB", "12")
    rows = run("regex_extract", {"column": "code", "pattern": "\\d+", "new_column": "num"})
    assert rows[2]["num"] == "301"


def test_date_ops() -> None:
    rows = run("date_parts", {"column": "order_date", "parts": ["year", "month", "year_month", "weekday_name"]})
    assert rows[0]["order_date_year"] == 2025 and rows[0]["order_date_year_month"] == "2025-01"
    rows = run("date_diff", {"start": "order_date", "end": "order_date", "unit": "day", "new_column": "d"})
    assert rows[0]["d"] == 0
    rows = run("parse_date", {"column": "status", "new_column": "p"})
    assert rows[0]["p"] is None


# --- combine ---------------------------------------------------------------

def test_join_types() -> None:
    inner = run("join", {"how": "inner", "keys": [{"left": "region", "right": "region"}]}, right=True)
    assert {r["manager"] for r in inner} == {"Nora", "Sam"} and len(inner) == 6
    left = run("join", {"how": "left", "keys": [{"left": "region", "right": "region"}]}, right=True)
    assert len(left) == 8
    anti = run("join", {"how": "anti", "keys": [{"left": "region", "right": "region"}]}, right=True)
    assert sorted(ids(anti)) == [4, 7]
    full = run("join", {"how": "full", "keys": [{"left": "region", "right": "region"}]}, right=True)
    assert "West" in {r["region"] for r in full}


def test_union() -> None:
    rows = run("union", {"by_name": True}, right=True)
    assert len(rows) == 11 and "manager" in rows[0]


# --- reshape ---------------------------------------------------------------

def test_aggregate() -> None:
    rows = run("aggregate", {"group_by": ["region"], "metrics": [
        {"function": "sum", "column": "order_total", "alias": "revenue"},
        {"function": "count"},
        {"function": "avg", "column": "order_total"}]})
    by = {r["region"]: r for r in rows}
    assert by["North"]["revenue"] == 635.5 and by["North"]["row_count"] == 4
    assert "avg_order_total" in by["North"]
    with pytest.raises(OpError, match="not numeric"):
        run("aggregate", {"metrics": [{"function": "sum", "column": "region"}]})


def test_pivot_unpivot() -> None:
    rows = run("pivot", {"index": ["region"], "pivot_column": "status", "value_column": "order_total"})
    north = next(r for r in rows if r["region"] == "North")
    assert north["delivered"] == 560.0
    rows = run("unpivot", {"id_columns": ["order_id"], "value_columns": ["order_total", "discount"]})
    assert {r["variable"] for r in rows} == {"order_total", "discount"}


def test_window() -> None:
    rows = run("window", {"function": "running_sum", "column": "order_total",
                          "partition_by": ["customer"], "order_by": "order_date", "new_column": "rt"})
    alice = [r["rt"] for r in rows if r["customer"] == "alice"]
    assert alice == [120.0, 340.0, 560.0]
    rows = run("window", {"function": "rank", "order_by": "order_total", "direction": "desc"})
    assert rows[0]["rank"] == 1
    with pytest.raises(OpError, match="order-by"):
        run("window", {"function": "lag", "column": "order_total"})


def test_bin() -> None:
    rows = run("bin_column", {"column": "order_total", "method": "custom", "edges": "0, 50, 100, 1000",
                              "labels": "low, mid, high"})
    assert [r["order_total_bin"] for r in rows[:4]] == ["high", "low", "mid", "high"]
    rows = run("bin_column", {"column": "order_total", "method": "equal_width", "bins": 2, "labels": "lo, hi"})
    assert rows[3]["order_total_bin"] == "hi" and rows[0]["order_total_bin"] == "lo"
    rows = run("bin_column", {"column": "order_total", "method": "quantile", "bins": 4})
    assert {r["order_total_bin"] for r in rows} <= {"Q1", "Q2", "Q3", "Q4"}


# --- quality ---------------------------------------------------------------

def test_profile() -> None:
    rows = run("profile", {})
    disc = next(r for r in rows if r["column_name"] == "discount")
    assert disc["null_count"] == 3 and disc["row_count"] == 8


def test_validate_modes() -> None:
    rules = [{"column": "region", "check": "not_null"},
             {"column": "order_total", "check": "min", "value": "20"}]
    flagged = run("validate", {"rules": rules})
    assert [r["_valid"] for r in flagged].count(False) == 1
    assert "region is empty" in flagged[-1]["_errors"]
    assert len(run("validate", {"rules": rules, "on_fail": "drop"})) == 7
    with pytest.raises(OpError, match="validation failed"):
        run("validate", {"rules": rules, "on_fail": "fail"})


# --- sandbox and dry run ---------------------------------------------------

def test_sandbox_blocks_files() -> None:
    from prompt_etl.ops.runner import sandboxed_connection

    with tempfile.TemporaryDirectory() as d:
        con = sandboxed_connection([d])
        with pytest.raises(duckdb.Error):
            con.execute("SELECT * FROM read_csv('/etc/passwd')").fetchall()
        with pytest.raises(duckdb.Error):
            con.execute("SET enable_external_access = true")


SCHEMA = [("order_id", "INT64"), ("region", "BYTE_ARRAY"), ("order_date", "date32[day]"),
          ("order_total", "double")]


def test_dry_run_schemas() -> None:
    out = dry_run_schema(get_op("aggregate"), {"group_by": ["region"], "metrics": [
        {"function": "sum", "column": "order_total", "alias": "revenue"}]}, [SCHEMA])
    assert out == [("region", "VARCHAR"), ("revenue", "DOUBLE")]
    out = dry_run_schema(get_op("date_parts"), {"column": "order_date", "parts": ["year"]}, [SCHEMA])
    assert out[-1][0] == "order_date_year"
    assert dry_run_schema(get_op("pivot"), {"pivot_column": "region", "value_column": "order_total"}, [SCHEMA]) is None
    with pytest.raises(OpError, match="unknown column"):
        dry_run_schema(get_op("select"), {"columns": ["nope"]}, [SCHEMA])
    out = dry_run_schema(get_op("predict_regression"), {"target": "order_total"}, [SCHEMA])
    assert out[-2:] == [("prediction", "DOUBLE"), ("split", "VARCHAR")]


def test_catalog_complete() -> None:
    from prompt_etl.ops import catalog_json

    cat = catalog_json()
    ids_ = {o["id"] for o in cat["operations"]}
    for legacy in ["filter", "select", "drop_nulls", "fill_nulls", "rename", "deduplicate", "aggregate", "sort", "custom"]:
        assert legacy in ids_
    assert len(ids_) >= 35
    for op in cat["operations"]:
        if op["engine"] == "sql":
            assert get_op(op["id"]).compile is not None
