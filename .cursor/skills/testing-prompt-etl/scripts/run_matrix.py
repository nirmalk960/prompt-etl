#!/usr/bin/env python3
"""Full functional test matrix for a running Prompt ETL instance.

Drives the public API exactly like the UI does (multipart upload -> ingest ->
pipeline job -> export) and checks the *data* that comes back, not only the
job status. Standard library only.

Examples:
  run_matrix.py --api http://localhost:8090/api
  run_matrix.py --api http://localhost:8090/api --only ops,chains --engines duckdb
  run_matrix.py --api http://localhost:8090/api --heavy-mb 50,500 --container prompt-etl-test
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable
from xml.sax.saxutils import escape

ENGINES = ("pandas", "polars", "duckdb")
CATEGORIES = (
    "platform", "ingest", "ops", "chains", "configure", "preflight", "ml", "dashboard",
    "export", "routing", "validation", "lifecycle", "llm", "scale", "persistence",
)
COLUMNS = [
    "order_id", "customer_id", "order_status", "order_date",
    "order_total", "discount_amount", "region",
]
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


class Skip(Exception):
    pass


class ApiError(Exception):
    pass


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class Api:
    def __init__(self, base: str, email: str, timeout: float) -> None:
        self.base = base.rstrip("/")
        token = os.environ.get("PROMPT_ETL_ACCESS_TOKEN") or os.environ.get("ACCESS_TOKEN")
        self.auth = f"Bearer {token}" if token else f"Bearer dev:{email}"
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        raw: bytes | None = None,
        auth: bool = True,
        timeout: float | None = None,
    ) -> tuple[int, Any, bytes]:
        absolute = path.startswith("http://") or path.startswith("https://")
        url = path if absolute else self.base + path
        headers: dict[str, str] = {}
        # Presigned S3/MinIO URLs reject an extra Authorization header.
        if auth and not absolute:
            headers["Authorization"] = self.auth
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif raw is not None:
            data = raw
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def json(self, method: str, path: str, body: Any = None, expect: tuple[int, ...] = (200, 201)) -> Any:
        status, _, payload = self.request(method, path, body=body)
        if status not in expect:
            raise ApiError(f"{method} {path} -> {status}: {payload[:400]!r}")
        return json.loads(payload) if payload else None


# ---------------------------------------------------------------------------
# Fixtures (deterministic) and expectations
# ---------------------------------------------------------------------------

def build_rows(n: int = 240, dupes: int = 10) -> list[dict[str, Any]]:
    statuses = ("completed", "pending", "cancelled", "refunded")
    regions = ("EU", "US", "APAC", "LATAM")
    rows: list[dict[str, Any]] = []
    for i in range(1, n + 1):
        month = (i % 12) + 1
        day = (i % 28) + 1
        rows.append({
            "order_id": f"ORD{i:05d}",
            "customer_id": f"C{(i % 37) + 1:04d}",
            "order_status": statuses[i % len(statuses)],
            "order_date": f"2025-{month:02d}-{day:02d}",
            "order_total": round(10 + (i * 7.3) % 150, 2),
            "discount_amount": None if i % 10 == 0 else round((i % 8) * 1.25, 2),
            "region": regions[i % len(regions)],
        })
    rows.extend(dict(r) for r in rows[:dupes])
    return rows


def to_csv(rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    for r in rows:
        w.writerow(["" if r[c] is None else r[c] for c in COLUMNS])
    return buf.getvalue().encode()


def to_ndjson(rows: list[dict[str, Any]]) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in rows).encode()


def to_xlsx(rows: list[dict[str, Any]]) -> bytes:
    """Minimal valid XLSX written with zipfile (no openpyxl on the host)."""
    def col_letter(i: int) -> str:
        return chr(ord("A") + i)

    def cell(ref: str, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (int, float)):
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'

    sheet_rows = []
    header = "".join(cell(f"{col_letter(i)}1", c) for i, c in enumerate(COLUMNS))
    sheet_rows.append(f'<row r="1">{header}</row>')
    for ri, r in enumerate(rows, start=2):
        cells = "".join(cell(f"{col_letter(i)}{ri}", r[c]) for i, c in enumerate(COLUMNS))
        sheet_rows.append(f'<row r="{ri}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>'
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>'
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="orders" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>'
        ),
        "xl/worksheets/sheet1.xml": sheet,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def close(a: float, b: float, tol: float = 0.01) -> bool:
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=tol)


def group_expect(rows: list[dict[str, Any]], key: str, col: str, fn: str) -> dict[str, float]:
    buckets: dict[str, list[float]] = {}
    for r in rows:
        v = r[col]
        if v is None:
            continue
        buckets.setdefault(str(r[key]), []).append(float(v))
    if fn == "sum":
        return {k: sum(v) for k, v in buckets.items()}
    if fn == "avg":
        return {k: sum(v) / len(v) for k, v in buckets.items()}
    raise ValueError(fn)


# ---------------------------------------------------------------------------
# Upload / pipeline helpers
# ---------------------------------------------------------------------------

def upload(api: Api, name: str, fmt: str, payload: bytes) -> str:
    init = api.json("POST", "/datasets/init", {"name": name, "source_format": fmt, "size_bytes": len(payload)})
    part_size = int(init["part_size_bytes"])
    completed = []
    for part in init["parts"]:
        pn = int(part["part_number"])
        chunk = payload[(pn - 1) * part_size: pn * part_size]
        status, headers, body = api.request("PUT", part["url"], raw=chunk, timeout=600)
        if status not in (200, 201, 204):
            raise ApiError(f"part {pn} upload -> {status}: {body[:200]!r}")
        etag = (headers.get("ETag") or headers.get("etag") or "").replace('"', "")
        if not etag:
            raise ApiError(f"part {pn}: storage returned no ETag")
        completed.append({"part_number": pn, "etag": etag})
    api.json("POST", f"/datasets/{init['dataset_id']}/complete", {"upload_id": init["upload_id"], "parts": completed})
    return init["dataset_id"]


def wait_dataset(api: Api, dataset_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        ds = api.json("GET", f"/datasets/{dataset_id}")
        if ds["status"] == "ready":
            return ds
        if ds["status"] == "failed":
            raise AssertionError(f"ingest failed: {ds.get('error')}")
        time.sleep(1)
    raise AssertionError(f"ingest timeout after {timeout:.0f}s")


def linear(dataset_id: str, steps: list[tuple[str, str]]) -> tuple[list[dict], list[dict]]:
    nodes = [{"id": "s0", "kind": "source", "label": "source", "dataset_id": dataset_id}]
    for i, (kind, prompt) in enumerate(steps, start=1):
        nodes.append({"id": f"n{i}", "kind": kind, "label": kind, "prompt": prompt})
    nodes.append({"id": "w0", "kind": "write", "label": "write"})
    edges = [{"source": nodes[i]["id"], "target": nodes[i + 1]["id"]} for i in range(len(nodes) - 1)]
    return nodes, edges


def submit(api: Api, nodes: list[dict], edges: list[dict], engine: str) -> str:
    job = api.json("POST", "/jobs", {"definition": {"nodes": nodes, "edges": edges, "engine": engine}})
    return job["id"]


def wait_job(api: Api, job_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = api.json("GET", f"/jobs/{job_id}")
        if detail["status"] in ("succeeded", "failed", "cancelled"):
            return detail
        time.sleep(1)
    raise AssertionError(f"job {job_id} still running after {timeout:.0f}s")


def run_ok(ctx: "Ctx", steps: list[tuple[str, str]], engine: str, dataset_id: str | None = None,
           timeout: float | None = None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    nodes, edges = linear(dataset_id or ctx.require("csv_dataset"), steps)
    detail = wait_job(ctx.api, submit(ctx.api, nodes, edges, engine), timeout or ctx.args.job_timeout)
    if detail["status"] != "succeeded":
        raise AssertionError(f"job {detail['status']}: {detail.get('error')}")
    if engine != "auto" and detail.get("engine") != engine:
        raise AssertionError(f"pinned engine={engine} but job ran on {detail.get('engine')}")
    return detail, export_rows(ctx.api, detail["result_dataset_id"])


def export_rows(api: Api, dataset_id: str) -> list[dict[str, str]]:
    status, _, body = api.request("GET", f"/datasets/{dataset_id}/export?fmt=csv", timeout=300)
    if status != 200:
        raise ApiError(f"export csv -> {status}: {body[:200]!r}")
    return list(csv.DictReader(io.StringIO(body.decode())))


def headers_of(rows: list[dict[str, str]]) -> set[str]:
    return set(rows[0].keys()) if rows else set()


def find_metric_col(rows: list[dict[str, str]], exclude: set[str]) -> str:
    for c in rows[0]:
        if c not in exclude and all(num(r[c]) is not None for r in rows):
            return c
    raise AssertionError(f"no numeric metric column in {list(rows[0])}")


# ---------------------------------------------------------------------------
# Test registry
# ---------------------------------------------------------------------------

@dataclass
class Result:
    category: str
    name: str
    status: str
    seconds: float
    detail: str = ""


@dataclass
class Ctx:
    api: Api
    args: argparse.Namespace
    rows: list[dict[str, Any]]
    state: dict[str, Any] = field(default_factory=dict)

    def require(self, key: str) -> Any:
        if key not in self.state:
            raise Skip(f"prerequisite '{key}' unavailable (earlier step failed or skipped)")
        return self.state[key]

    @property
    def unique_rows(self) -> list[dict[str, Any]]:
        seen, out = set(), []
        for r in self.rows:
            k = tuple(r[c] for c in COLUMNS)
            if k not in seen:
                seen.add(k)
                out.append(r)
        return out


TESTS: list[tuple[str, str, Callable[[Ctx], str | None]]] = []


def test(category: str, name: str):
    def deco(fn):
        TESTS.append((category, name, fn))
        return fn
    return deco


# ---- platform ---------------------------------------------------------------

@test("platform", "health endpoint")
def _(ctx: Ctx):
    assert ctx.api.json("GET", "/health")["status"] == "ok"


@test("platform", "ready endpoint reports resources")
def _(ctx: Ctx):
    r = ctx.api.json("GET", "/ready")
    for key in ("ram_bytes", "cpus", "ollama", "object_store"):
        assert key in r, f"missing {key}"
    ctx.state["ollama"] = bool(r["ollama"]["available"]) and not r["ollama"]["disabled"]
    return (f"ram={r['ram_bytes'] / 2**30:.1f}GiB cpus={r['cpus']} store={r['object_store']} "
            f"ollama={r['ollama']['available']} model={r['ollama']['model']}")


@test("platform", "UI is served")
def _(ctx: Ctx):
    ui = ctx.args.ui or (ctx.api.base[:-4] + "/" if ctx.api.base.endswith("/api") else None)
    if not ui:
        raise Skip("pass --ui for the dev stack")
    status, _, body = ctx.api.request("GET", ui, auth=False)
    assert status == 200, f"UI returned {status}"
    assert b"<html" in body.lower() or b'id="root"' in body, "UI body is not HTML"


@test("platform", "dev auth identity")
def _(ctx: Ctx):
    assert ctx.api.json("GET", "/auth/me"), "empty /auth/me"


@test("platform", "requests without auth are rejected")
def _(ctx: Ctx):
    status, _, _ = ctx.api.request("GET", "/datasets", auth=False)
    assert status in (401, 403), f"expected 401/403, got {status}"


# ---- ingest -----------------------------------------------------------------

def _ingest(ctx: Ctx, fmt: str, payload: bytes, name: str) -> str:
    ds_id = upload(ctx.api, name, fmt, payload)
    ds = wait_dataset(ctx.api, ds_id, ctx.args.ingest_timeout)
    assert ds["row_count"] == len(ctx.rows), f"row_count={ds['row_count']} expected {len(ctx.rows)}"
    cols = [f["name"] for f in (ds.get("schema_json") or {}).get("fields", [])]
    missing = [c for c in COLUMNS if c not in cols]
    assert not missing, f"schema missing {missing} (got {cols})"
    prev = ctx.api.json("GET", f"/datasets/{ds_id}/preview?n=5")
    assert len(prev["rows"]) == 5, f"preview returned {len(prev['rows'])} rows"
    ctx.state[f"{fmt}_dataset"] = ds_id
    return f"dataset={ds_id} rows={ds['row_count']}"


@test("ingest", "CSV upload -> parquet")
def _(ctx: Ctx):
    return _ingest(ctx, "csv", to_csv(ctx.rows), "matrix_orders.csv")


@test("ingest", "NDJSON upload -> parquet")
def _(ctx: Ctx):
    return _ingest(ctx, "json", to_ndjson(ctx.rows), "matrix_orders.json")


@test("ingest", "XLSX upload -> parquet")
def _(ctx: Ctx):
    return _ingest(ctx, "xlsx", to_xlsx(ctx.rows), "matrix_orders.xlsx")


@test("ingest", "Parquet upload (round-trip from export)")
def _(ctx: Ctx):
    src = ctx.require("csv_dataset")
    status, _, body = ctx.api.request("GET", f"/datasets/{src}/export?fmt=parquet")
    assert status == 200 and body[:4] == b"PAR1", f"parquet export failed ({status})"
    return _ingest(ctx, "parquet", body, "matrix_orders.parquet")


@test("ingest", "multipart upload with several parts")
def _(ctx: Ctx):
    payload = to_csv(ctx.rows * 400)
    init = ctx.api.json("POST", "/datasets/init", {"name": "probe.csv", "source_format": "csv", "size_bytes": len(payload)})
    part_size = int(init["part_size_bytes"])
    if len(payload) <= part_size:
        raise Skip(f"part size {part_size} > fixture; covered by scale tests")
    ds_id = upload(ctx.api, "matrix_multipart.csv", "csv", payload)
    ds = wait_dataset(ctx.api, ds_id, ctx.args.ingest_timeout)
    assert ds["row_count"] == len(ctx.rows) * 400


# ---- single operations x engines -------------------------------------------

def _ops_for(engine: str) -> None:
    @test("ops", f"[{engine}] filter equals")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("filter", "Filter rows where region equals EU")], engine)
        exp = sum(1 for r in ctx.rows if r["region"] == "EU")
        assert len(out) == exp, f"{len(out)} rows, expected {exp}"
        assert all(r["region"] == "EU" for r in out), "non-EU rows leaked"

    @test("ops", f"[{engine}] filter not equals")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("filter", "Filter rows where region not equals EU")], engine)
        exp = sum(1 for r in ctx.rows if r["region"] != "EU")
        assert len(out) == exp, f"{len(out)} rows, expected {exp}"
        assert all(r["region"] != "EU" for r in out), "EU rows leaked"

    @test("ops", f"[{engine}] filter greater than")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("filter", "Filter rows where order_total > 50")], engine)
        exp = sum(1 for r in ctx.rows if r["order_total"] > 50)
        assert len(out) == exp, f"{len(out)} rows, expected {exp}"
        assert all(num(r["order_total"]) > 50 for r in out), "values <= 50 leaked"

    @test("ops", f"[{engine}] filter less than")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("filter", "Filter rows where order_total < 30")], engine)
        exp = sum(1 for r in ctx.rows if r["order_total"] < 30)
        assert len(out) == exp, f"{len(out)} rows, expected {exp}"

    @test("ops", f"[{engine}] filter worded: 'is greater than'")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("filter", "Filter rows where order_total is greater than 50")], engine)
        exp = sum(1 for r in ctx.rows if r["order_total"] > 50)
        assert len(out) == exp, f"{len(out)} rows, expected {exp}"

    @test("ops", f"[{engine}] filter worded: 'is at least'")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("filter", "Keep rows where order_total is at least 50")], engine)
        exp = sum(1 for r in ctx.rows if r["order_total"] >= 50)
        assert len(out) == exp, f"{len(out)} rows, expected {exp}"

    @test("ops", f"[{engine}] filter worded: 'is not'")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("filter", "Filter rows where region is not EU")], engine)
        exp = sum(1 for r in ctx.rows if r["region"] != "EU")
        assert len(out) == exp, f"{len(out)} rows, expected {exp}"

    @test("ops", f"[{engine}] select columns")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("select", "Keep columns order_id, region, order_total")], engine)
        assert headers_of(out) == {"order_id", "region", "order_total"}, f"columns {headers_of(out)}"
        assert len(out) == len(ctx.rows)

    @test("ops", f"[{engine}] rename column")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("rename", "Rename column region to market")], engine)
        h = headers_of(out)
        assert "market" in h and "region" not in h, f"columns {sorted(h)}"
        assert {r["market"] for r in out} == {r["region"] for r in ctx.rows}

    @test("ops", f"[{engine}] drop nulls in one column")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("drop_nulls", "Drop rows with null values in discount_amount")], engine)
        exp = sum(1 for r in ctx.rows if r["discount_amount"] is not None)
        assert len(out) == exp, f"{len(out)} rows, expected {exp}"
        assert all(r["discount_amount"] != "" for r in out), "null discounts remain"

    @test("ops", f"[{engine}] drop nulls in any column")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("drop_nulls", "Drop rows with null values")], engine)
        exp = sum(1 for r in ctx.rows if all(r[c] is not None for c in COLUMNS))
        assert len(out) == exp, f"{len(out)} rows, expected {exp} (nulls not dropped)"

    @test("ops", f"[{engine}] deduplicate on key keeps all columns")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("deduplicate", "Remove duplicates on order_id")], engine)
        exp = len({r["order_id"] for r in ctx.rows})
        assert len(out) == exp, f"{len(out)} rows, expected {exp}"
        assert headers_of(out) == set(COLUMNS), f"columns dropped: {sorted(headers_of(out))}"

    @test("ops", f"[{engine}] deduplicate full rows")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("deduplicate", "Remove duplicate rows")], engine)
        assert len(out) == len(ctx.unique_rows), f"{len(out)} rows, expected {len(ctx.unique_rows)}"

    @test("ops", f"[{engine}] aggregate simple (sum by)")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("aggregate", "Sum of order_total by region")], engine)
        exp = group_expect(ctx.rows, "region", "order_total", "sum")
        assert len(out) == len(exp), f"{len(out)} groups, expected {len(exp)}"
        metric = find_metric_col(out, {"region"})
        for r in out:
            assert close(num(r[metric]), exp[r["region"]]), f"{r['region']}: {r[metric]} != {exp[r['region']]:.2f}"
        return f"metric column={metric}"

    @test("ops", f"[{engine}] aggregate with aliases")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("aggregate",
            "Group by region and calculate sum(order_total) as total_revenue, avg(order_total) as avg_order_value")], engine)
        h = headers_of(out)
        assert {"region", "total_revenue", "avg_order_value"} <= h, f"columns {sorted(h)}"
        s = group_expect(ctx.rows, "region", "order_total", "sum")
        a = group_expect(ctx.rows, "region", "order_total", "avg")
        for r in out:
            assert close(num(r["total_revenue"]), s[r["region"]]), f"total_revenue {r['region']} wrong"
            assert close(num(r["avg_order_value"]), a[r["region"]]), f"avg_order_value {r['region']} wrong"

    @test("ops", f"[{engine}] aggregate by month()")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("aggregate",
            "Group by month(order_date) and region and calculate total_revenue (sum order_total)")], engine)
        h = headers_of(out)
        assert {"month", "region", "total_revenue"} <= h, f"columns {sorted(h)}"
        assert all(len(r["month"]) == 7 and r["month"][4] == "-" for r in out), "month not YYYY-MM"
        total = sum(num(r["total_revenue"]) for r in out)
        assert close(total, sum(r["order_total"] for r in ctx.rows), tol=0.5), "revenue total mismatch"

    @test("ops", f"[{engine}] sort descending")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("sort", "Sort by order_total descending")], engine)
        vals = [num(r["order_total"]) for r in out]
        assert vals == sorted(vals, reverse=True), "not sorted descending"
        assert len(out) == len(ctx.rows)

    @test("ops", f"[{engine}] sort ascending")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [("sort", "Sort by order_date ascending")], engine)
        vals = [r["order_date"] for r in out]
        assert vals == sorted(vals), "not sorted ascending"


# ---- multi-step chains ------------------------------------------------------

def _chains_for(engine: str) -> None:
    @test("chains", f"[{engine}] demo: filter -> aggregate -> sort")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [
            ("filter", "Filter rows where order_total is greater than 50"),
            ("aggregate", "Group by region and calculate sum(order_total) as total_revenue, avg(order_total) as avg_order_value"),
            ("sort", "Sort by total_revenue descending"),
        ], engine)
        kept = [r for r in ctx.rows if r["order_total"] > 50]
        exp = group_expect(kept, "region", "order_total", "sum")
        vals = [num(r["total_revenue"]) for r in out]
        assert vals == sorted(vals, reverse=True), "not sorted by total_revenue desc"
        for r in out:
            assert close(num(r["total_revenue"]), exp[r["region"]]), f"{r['region']} revenue wrong"

    @test("chains", f"[{engine}] cleaning: drop nulls -> dedupe -> select -> rename -> sort")
    def _(ctx: Ctx):
        _, out = run_ok(ctx, [
            ("drop_nulls", "Drop rows with null values in discount_amount"),
            ("deduplicate", "Remove duplicate rows"),
            ("select", "Keep columns order_id, region, order_total, discount_amount"),
            ("rename", "Rename column order_total to amount"),
            ("sort", "Sort by amount descending"),
        ], engine)
        exp = sum(1 for r in ctx.unique_rows if r["discount_amount"] is not None)
        assert headers_of(out) == {"order_id", "region", "amount", "discount_amount"}, f"columns {sorted(headers_of(out))}"
        assert len(out) == exp, f"{len(out)} rows, expected {exp}"
        vals = [num(r["amount"]) for r in out]
        assert vals == sorted(vals, reverse=True), "not sorted"

    @test("chains", f"[{engine}] fan-out DAG (one source, two branches)")
    def _(ctx: Ctx):
        ds = ctx.require("csv_dataset")
        nodes = [
            {"id": "s1", "kind": "source", "dataset_id": ds},
            {"id": "f1", "kind": "filter", "prompt": "Filter rows where region equals EU"},
            {"id": "f2", "kind": "filter", "prompt": "Filter rows where region equals US"},
            {"id": "w1", "kind": "write"},
            {"id": "w2", "kind": "write"},
        ]
        edges = [{"source": "s1", "target": "f1"}, {"source": "s1", "target": "f2"},
                 {"source": "f1", "target": "w1"}, {"source": "f2", "target": "w2"}]
        detail = wait_job(ctx.api, submit(ctx.api, nodes, edges, engine), ctx.args.job_timeout)
        assert detail["status"] == "succeeded", f"fan-out DAG {detail['status']}: {detail.get('error')}"


# ---- export ----------------------------------------------------------------

@test("export", "CSV / NDJSON / TXT / Parquet downloads")
def _(ctx: Ctx):
    ds = ctx.require("csv_dataset")
    n = len(ctx.rows)
    checks = {}
    for fmt in ("csv", "json", "txt", "parquet"):
        status, _, body = ctx.api.request("GET", f"/datasets/{ds}/export?fmt={fmt}", timeout=300)
        assert status == 200, f"{fmt} -> {status}"
        if fmt == "csv":
            assert len(list(csv.DictReader(io.StringIO(body.decode())))) == n, "csv row count"
        elif fmt == "json":
            lines = [json.loads(l) for l in body.decode().splitlines() if l.strip()]
            assert len(lines) == n, f"json rows {len(lines)}"
        elif fmt == "txt":
            first = body.decode().splitlines()[0]
            assert "\t" in first and "order_id" in first, "txt not tab-delimited"
        else:
            assert body[:4] == b"PAR1" and body[-4:] == b"PAR1", "parquet magic missing"
        checks[fmt] = len(body)
    return " ".join(f"{k}={v}B" for k, v in checks.items())


@test("export", "invalid format is rejected")
def _(ctx: Ctx):
    status, _, _ = ctx.api.request("GET", f"/datasets/{ctx.require('csv_dataset')}/export?fmt=xml")
    assert status == 422, f"expected 422, got {status}"


@test("export", "unknown dataset is 404")
def _(ctx: Ctx):
    status, _, _ = ctx.api.request("GET", f"/datasets/{uuid.uuid4()}/export?fmt=csv")
    assert status == 404, f"expected 404, got {status}"


# ---- routing ----------------------------------------------------------------

@test("routing", "auto picks an engine and reports it")
def _(ctx: Ctx):
    detail, _ = run_ok(ctx, [("filter", "Filter rows where region equals EU")], "auto")
    selected = [e for e in detail.get("events", []) if e.get("kind") == "engine_selected"]
    assert detail.get("engine") in ENGINES, f"engine={detail.get('engine')}"
    assert selected, "no engine_selected event"
    return f"auto -> {detail['engine']}"


@test("routing", "spark is rejected with a clear error (Phase 1)")
def _(ctx: Ctx):
    nodes, edges = linear(ctx.require("csv_dataset"), [("filter", "Filter rows where region equals EU")])
    status, _, body = ctx.api.request("POST", "/jobs", body={"definition": {"nodes": nodes, "edges": edges, "engine": "spark"}})
    if status == 422:
        return "rejected at submit"
    assert status in (200, 201), f"submit -> {status}"
    detail = wait_job(ctx.api, json.loads(body)["id"], ctx.args.job_timeout)
    assert detail["status"] == "failed", f"spark job {detail['status']}"
    assert detail.get("error"), "spark failure has no error message"
    return detail["error"][:120]


# ---- validation (negative) -------------------------------------------------

@test("validation", "duplicate node ids -> 422")
def _(ctx: Ctx):
    nodes = [{"id": "a", "kind": "source"}, {"id": "a", "kind": "write"}]
    status, _, _ = ctx.api.request("POST", "/jobs", body={"definition": {"nodes": nodes, "edges": []}})
    assert status == 422, f"got {status}"


@test("validation", "edge to unknown node -> 422")
def _(ctx: Ctx):
    nodes = [{"id": "a", "kind": "source"}]
    status, _, _ = ctx.api.request("POST", "/jobs", body={"definition": {"nodes": nodes, "edges": [{"source": "a", "target": "ghost"}]}})
    assert status == 422, f"got {status}"


@test("validation", "unknown node kind -> 422")
def _(ctx: Ctx):
    nodes = [{"id": "a", "kind": "teleport"}]
    status, _, _ = ctx.api.request("POST", "/jobs", body={"definition": {"nodes": nodes, "edges": []}})
    assert status == 422, f"got {status}"


@test("validation", "filter on missing column fails clearly")
def _(ctx: Ctx):
    nodes, edges = linear(ctx.require("csv_dataset"), [("filter", "Filter rows where no_such_column equals X")])
    detail = wait_job(ctx.api, submit(ctx.api, nodes, edges, "pandas"), ctx.args.job_timeout)
    assert detail["status"] == "failed", f"job {detail['status']} (should fail)"
    assert detail.get("error"), "no error message"
    return detail["error"][:120]


@test("validation", "unparseable prompt without LLM fails clearly")
def _(ctx: Ctx):
    if ctx.state.get("ollama"):
        raise Skip("Ollama available; covered by llm tests")
    nodes, edges = linear(ctx.require("csv_dataset"), [("filter", "keep the good vibes only")])
    detail = wait_job(ctx.api, submit(ctx.api, nodes, edges, "pandas"), ctx.args.job_timeout)
    assert detail["status"] == "failed", f"job {detail['status']}"
    return (detail.get("error") or "")[:120]


@test("validation", "source without dataset fails")
def _(ctx: Ctx):
    nodes = [{"id": "s", "kind": "source"}, {"id": "w", "kind": "write"}]
    status, _, body = ctx.api.request("POST", "/jobs", body={"definition": {"nodes": nodes, "edges": [{"source": "s", "target": "w"}]}})
    if status == 422:
        return "rejected at submit"
    assert status in (200, 201), f"submit -> {status}"
    detail = wait_job(ctx.api, json.loads(body)["id"], ctx.args.job_timeout)
    assert detail["status"] == "failed", f"job {detail['status']}"


# ---- lifecycle --------------------------------------------------------------

@test("lifecycle", "job list, detail and events")
def _(ctx: Ctx):
    detail, _ = run_ok(ctx, [("sort", "Sort by order_total descending")], "pandas")
    ctx.state["finished_job"] = detail["id"]
    ids = {j["id"] for j in ctx.api.json("GET", "/jobs")}
    assert detail["id"] in ids, "job missing from list"
    kinds = [e.get("kind") for e in detail.get("events", [])]
    for k in ("job_started", "engine_selected", "step_started", "step_completed", "job_succeeded"):
        assert k in kinds, f"missing event {k} (got {kinds})"


@test("lifecycle", "SSE stream replays events")
def _(ctx: Ctx):
    job_id = ctx.require("finished_job")
    url = ctx.api.base + f"/jobs/{job_id}/events"
    req = urllib.request.Request(url, headers={"Authorization": ctx.api.auth, "Accept": "text/event-stream"})
    seen: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            assert "text/event-stream" in r.headers.get("Content-Type", ""), "wrong content type"
            deadline = time.time() + 10
            while time.time() < deadline:
                line = r.readline().decode().strip()
                if line.startswith("event:"):
                    seen.append(line.split(":", 1)[1].strip())
                if "job_succeeded" in seen:
                    break
    except TimeoutError:
        pass
    assert "job_succeeded" in seen, f"SSE events seen: {seen}"


@test("lifecycle", "cancel a job")
def _(ctx: Ctx):
    nodes, edges = linear(ctx.require("csv_dataset"), [("sort", "Sort by order_total descending")])
    job_id = submit(ctx.api, nodes, edges, "pandas")
    summary = ctx.api.json("POST", f"/jobs/{job_id}/cancel")
    assert summary["status"] in ("cancelled", "succeeded", "failed"), f"status {summary['status']}"
    again = ctx.api.json("POST", f"/jobs/{job_id}/cancel")
    assert again["status"] == summary["status"] or again["status"] in ("succeeded", "failed", "cancelled")
    return f"status after cancel={summary['status']}"


@test("lifecycle", "workflow create/get/update/list/delete")
def _(ctx: Ctx):
    nodes, edges = linear(ctx.require("csv_dataset"), [("filter", "Filter rows where region equals EU")])
    wf = ctx.api.json("POST", "/workflows", {"name": "matrix workflow", "definition": {"nodes": nodes, "edges": edges}})
    got = ctx.api.json("GET", f"/workflows/{wf['id']}")
    assert got["name"] == "matrix workflow"
    upd = ctx.api.json("PUT", f"/workflows/{wf['id']}", {"name": "matrix workflow v2", "definition": {"nodes": nodes, "edges": edges}})
    assert upd["name"] == "matrix workflow v2"
    assert wf["id"] in {w["id"] for w in ctx.api.json("GET", "/workflows")}
    status, _, _ = ctx.api.request("DELETE", f"/workflows/{wf['id']}")
    assert status == 204, f"delete -> {status}"
    status, _, _ = ctx.api.request("GET", f"/workflows/{wf['id']}")
    assert status == 404, f"deleted workflow still readable ({status})"


@test("lifecycle", "concurrent jobs all complete")
def _(ctx: Ctx):
    ds = ctx.require("csv_dataset")
    ids = []
    for engine in ENGINES:
        nodes, edges = linear(ds, [("aggregate", "Sum of order_total by region")])
        ids.append(submit(ctx.api, nodes, edges, engine))
    results = [wait_job(ctx.api, i, ctx.args.job_timeout)["status"] for i in ids]
    assert all(s == "succeeded" for s in results), f"statuses {results}"


# ---- LLM-dependent ----------------------------------------------------------

def _llm_for(engine: str) -> None:
    def need(ctx: Ctx) -> None:
        if not ctx.state.get("ollama"):
            raise Skip("Ollama not available to the container")

    @test("llm", f"[{engine}] fill nulls")
    def _(ctx: Ctx):
        need(ctx)
        _, out = run_ok(ctx, [("fill_nulls", "Fill null values in discount_amount with 0")], engine,
                        timeout=ctx.args.llm_timeout)
        assert len(out) == len(ctx.rows)
        assert all(r["discount_amount"] != "" for r in out), "nulls remain"

    @test("llm", f"[{engine}] custom computed column")
    def _(ctx: Ctx):
        need(ctx)
        _, out = run_ok(ctx, [("custom", "Add a column net_total equal to order_total minus discount_amount")],
                        engine, timeout=ctx.args.llm_timeout)
        assert "net_total" in headers_of(out), f"columns {sorted(headers_of(out))}"
        for r in out:
            t, d, n = num(r["order_total"]), num(r["discount_amount"]), num(r["net_total"])
            if t is not None and d is not None and n is not None:
                assert close(n, t - d), f"net_total {n} != {t} - {d}"

    @test("llm", f"[{engine}] natural-language filter")
    def _(ctx: Ctx):
        need(ctx)
        _, out = run_ok(ctx, [("filter", "Keep only orders where order_total is greater than 50 and region is EU")],
                        engine, timeout=ctx.args.llm_timeout)
        exp = sum(1 for r in ctx.rows if r["order_total"] > 50 and r["region"] == "EU")
        assert len(out) == exp, f"{len(out)} rows, expected {exp}"

    @test("llm", f"[{engine}] natural-language aggregate")
    def _(ctx: Ctx):
        need(ctx)
        _, out = run_ok(ctx, [("aggregate",
            "Group by region and calculate total_revenue as the sum of order_total and avg_order_value as the average of order_total")],
            engine, timeout=ctx.args.llm_timeout)
        assert {"region", "total_revenue"} <= headers_of(out), f"columns {sorted(headers_of(out))}"


# ---- configured operations (no LLM) -----------------------------------------

def cfg_pipeline(dataset_id: str, steps: list[tuple[str, dict]]) -> tuple[list[dict], list[dict]]:
    nodes = [{"id": "s0", "kind": "source", "label": "source", "dataset_id": dataset_id}]
    for i, (kind, config) in enumerate(steps, start=1):
        nodes.append({"id": f"n{i}", "kind": kind, "label": kind, "mode": "configure", "config": config})
    nodes.append({"id": "w0", "kind": "write", "label": "write"})
    edges = [{"source": nodes[i]["id"], "target": nodes[i + 1]["id"]} for i in range(len(nodes) - 1)]
    return nodes, edges


def step_events(detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["payload"]["node_id"]: e["payload"] for e in detail.get("events", [])
            if e.get("kind") == "step_completed"}


def run_cfg(ctx: Ctx, steps: list[tuple[str, dict]], dataset_id: str | None = None,
            nodes_edges: tuple[list[dict], list[dict]] | None = None) -> tuple[dict, list[dict[str, str]]]:
    nodes, edges = nodes_edges or cfg_pipeline(dataset_id or ctx.require("csv_dataset"), steps)
    detail = wait_job(ctx.api, submit(ctx.api, nodes, edges, "auto"), ctx.args.job_timeout)
    if detail["status"] != "succeeded":
        raise AssertionError(f"job {detail['status']}: {detail.get('error')}")
    for nid, ev in step_events(detail).items():
        if nid.startswith("n"):
            assert ev.get("mode") == "configure", f"{nid} ran in mode {ev.get('mode')}"
            assert ev.get("engine") in ("duckdb", "python"), f"{nid} engine {ev.get('engine')}"
    return detail, export_rows(ctx.api, detail["result_dataset_id"])


def run_cfg_fail(ctx: Ctx, steps: list[tuple[str, dict]]) -> str:
    nodes, edges = cfg_pipeline(ctx.require("csv_dataset"), steps)
    detail = wait_job(ctx.api, submit(ctx.api, nodes, edges, "auto"), ctx.args.job_timeout)
    assert detail["status"] == "failed", f"job {detail['status']} (should fail)"
    return detail.get("error") or ""


def cond(column: str, operator: str, value: Any = None, value2: Any = None) -> dict:
    out: dict[str, Any] = {"column": column, "operator": operator}
    if value is not None:
        out["value"] = value
    if value2 is not None:
        out["value2"] = value2
    return out


@test("configure", "operation catalog is served")
def _(ctx: Ctx):
    cat = ctx.api.json("GET", "/operations")
    ids = {o["id"] for o in cat["operations"]}
    for legacy in ("filter", "select", "drop_nulls", "fill_nulls", "rename", "deduplicate", "aggregate", "sort", "custom"):
        assert legacy in ids, f"legacy op {legacy} missing"
    for new in ("join", "pivot", "window", "predict_regression", "forecast", "dashboard"):
        assert new in ids, f"op {new} missing"
    return f"{len(ids)} operations in {len(cat['categories'])} categories"


@test("configure", "filter AND conditions")
def _(ctx: Ctx):
    _, out = run_cfg(ctx, [("filter", {"conditions": [cond("region", "=", "EU"), cond("order_total", ">", "50")],
                                       "match": "all"})])
    exp = sum(1 for r in ctx.rows if r["region"] == "EU" and r["order_total"] > 50)
    assert len(out) == exp, f"{len(out)} rows, expected {exp}"


@test("configure", "filter OR / in list / is empty")
def _(ctx: Ctx):
    _, out = run_cfg(ctx, [("filter", {"conditions": [cond("region", "in", "EU, US"),
                                                      cond("discount_amount", "is_null")], "match": "any"})])
    exp = sum(1 for r in ctx.rows if r["region"] in ("EU", "US") or r["discount_amount"] is None)
    assert len(out) == exp, f"{len(out)} rows, expected {exp}"


@test("configure", "text value against numeric column is rejected")
def _(ctx: Ctx):
    err = run_cfg_fail(ctx, [("filter", {"conditions": [cond("order_total", ">", "lots")]})])
    assert "numeric" in err, err
    return err[:100]


@test("configure", "select / drop / rename columns")
def _(ctx: Ctx):
    _, out = run_cfg(ctx, [
        ("select", {"columns": ["order_id", "region", "order_total", "discount_amount"]}),
        ("drop_columns", {"columns": ["discount_amount"]}),
        ("rename", {"renames": [{"from": "order_total", "to": "amount"}]}),
    ])
    assert list(out[0].keys()) == ["order_id", "region", "amount"], f"columns {list(out[0].keys())}"
    assert len(out) == len(ctx.rows)


@test("configure", "formula + conditional column")
def _(ctx: Ctx):
    _, out = run_cfg(ctx, [
        ("fill_nulls", {"fills": [{"column": "discount_amount", "method": "value", "value": "0"}]}),
        ("formula", {"new_column": "net_total", "expression": "order_total - discount_amount"}),
        ("conditional_column", {"new_column": "size",
                                "rules": [{**cond("order_total", ">=", "100"), "then": "big"}],
                                "else_value": "small"}),
    ])
    for r in out:
        t, d, n = num(r["order_total"]), num(r["discount_amount"]), num(r["net_total"])
        assert close(n, t - d), f"net_total {n} != {t} - {d}"
        assert r["size"] == ("big" if t >= 100 else "small"), f"size {r['size']} for {t}"


@test("configure", "fill nulls with mean")
def _(ctx: Ctx):
    _, out = run_cfg(ctx, [("fill_nulls", {"fills": [{"column": "discount_amount", "method": "mean"}]})])
    vals = [r["discount_amount"] for r in ctx.rows if r["discount_amount"] is not None]
    mean = sum(vals) / len(vals)
    filled = [num(r["discount_amount"]) for r, src in zip(out, ctx.rows) if src["discount_amount"] is None]
    assert filled and all(close(v, mean) for v in filled), f"filled {filled[:3]} expected {mean:.3f}"


@test("configure", "deduplicate on key + sort + top N")
def _(ctx: Ctx):
    _, out = run_cfg(ctx, [
        ("deduplicate", {"columns": ["order_id"]}),
        ("top_n", {"count": 5, "order_by": "order_total", "direction": "desc"}),
    ])
    exp = sorted({r["order_id"]: r["order_total"] for r in ctx.rows}.values(), reverse=True)[:5]
    got = [num(r["order_total"]) for r in out]
    assert len(out) == 5 and all(close(a, b) for a, b in zip(got, exp)), f"{got} != {exp}"


@test("configure", "aggregate with aliases, sorted")
def _(ctx: Ctx):
    _, out = run_cfg(ctx, [
        ("aggregate", {"group_by": ["region"], "metrics": [
            {"function": "sum", "column": "order_total", "alias": "total_revenue"},
            {"function": "count", "alias": "orders"}]}),
        ("sort", {"sorts": [{"column": "total_revenue", "direction": "desc"}]}),
    ])
    exp = group_expect(ctx.rows, "region", "order_total", "sum")
    vals = [num(r["total_revenue"]) for r in out]
    assert vals == sorted(vals, reverse=True), "not sorted"
    for r in out:
        assert close(num(r["total_revenue"]), exp[r["region"]]), f"{r['region']} revenue wrong"
        assert int(r["orders"]) == sum(1 for x in ctx.rows if x["region"] == r["region"])


@test("configure", "date parts -> monthly totals")
def _(ctx: Ctx):
    _, out = run_cfg(ctx, [
        ("date_parts", {"column": "order_date", "parts": ["year_month"]}),
        ("aggregate", {"group_by": ["order_date_year_month"],
                       "metrics": [{"function": "sum", "column": "order_total", "alias": "revenue"}]}),
    ])
    assert len(out) == 12, f"{len(out)} months"
    assert all(len(r["order_date_year_month"]) == 7 for r in out)
    assert close(sum(num(r["revenue"]) for r in out), sum(r["order_total"] for r in ctx.rows), tol=0.5)


@test("configure", "pivot regions x status")
def _(ctx: Ctx):
    _, out = run_cfg(ctx, [("pivot", {"index": ["region"], "pivot_column": "order_status",
                                      "value_column": "order_total", "function": "sum"})])
    assert len(out) == 4, f"{len(out)} rows"
    assert {"completed", "pending"} <= headers_of(out), f"columns {sorted(headers_of(out))}"


@test("configure", "window running total")
def _(ctx: Ctx):
    _, out = run_cfg(ctx, [("window", {"function": "running_sum", "column": "order_total",
                                       "order_by": "order_id", "new_column": "running"})])
    last = max(out, key=lambda r: num(r["running"]))
    assert close(num(last["running"]), sum(r["order_total"] for r in ctx.rows), tol=0.5)


@test("configure", "profile + validate (flag)")
def _(ctx: Ctx):
    _, prof = run_cfg(ctx, [("profile", {})])
    by_col = {r["column_name"]: r for r in prof}
    assert set(COLUMNS) <= set(by_col), f"profiled {sorted(by_col)}"
    assert int(by_col["discount_amount"]["null_count"]) == sum(1 for r in ctx.rows if r["discount_amount"] is None)
    _, out = run_cfg(ctx, [("validate", {"rules": [{"column": "discount_amount", "check": "not_null"}],
                                         "on_fail": "flag"})])
    bad = sum(1 for r in out if r["_valid"].lower() == "false")
    assert bad == sum(1 for r in ctx.rows if r["discount_amount"] is None), f"{bad} invalid rows"


@test("configure", "validate (fail) stops the pipeline")
def _(ctx: Ctx):
    err = run_cfg_fail(ctx, [("validate", {"rules": [{"column": "discount_amount", "check": "not_null"}],
                                            "on_fail": "fail"})])
    assert "validation failed" in err, err
    return err[:100]


@test("configure", "join orders with a lookup table (left/right inputs)")
def _(ctx: Ctx):
    lookup = b"region,manager\nEU,Anna\nUS,Bob\nAPAC,Chen\n"
    lk = upload(ctx.api, "matrix_regions.csv", "csv", lookup)
    wait_dataset(ctx.api, lk, ctx.args.ingest_timeout)
    nodes = [
        {"id": "s0", "kind": "source", "dataset_id": ctx.require("csv_dataset")},
        {"id": "s1", "kind": "source", "dataset_id": lk},
        {"id": "n1", "kind": "join", "mode": "configure",
         "config": {"how": "left", "keys": [{"left": "region", "right": "region"}]}},
        {"id": "w0", "kind": "write"},
    ]
    edges = [{"source": "s1", "target": "n1", "target_handle": "right"},
             {"source": "s0", "target": "n1", "target_handle": "left"},
             {"source": "n1", "target": "w0"}]
    _, out = run_cfg(ctx, [], nodes_edges=(nodes, edges))
    assert len(out) == len(ctx.rows), f"{len(out)} rows (left join must keep all)"
    managers = {"EU": "Anna", "US": "Bob", "APAC": "Chen", "LATAM": ""}
    assert all(r["manager"] == managers[r["region"]] for r in out), "wrong manager mapping"


@test("configure", "union of two branches")
def _(ctx: Ctx):
    ds = ctx.require("csv_dataset")
    nodes = [
        {"id": "s0", "kind": "source", "dataset_id": ds},
        {"id": "n1", "kind": "filter", "mode": "configure", "config": {"conditions": [cond("region", "=", "EU")]}},
        {"id": "n2", "kind": "filter", "mode": "configure", "config": {"conditions": [cond("region", "=", "US")]}},
        {"id": "n3", "kind": "union", "mode": "configure", "config": {}},
        {"id": "w0", "kind": "write"},
    ]
    edges = [{"source": "s0", "target": "n1"}, {"source": "s0", "target": "n2"},
             {"source": "n1", "target": "n3", "target_handle": "left"},
             {"source": "n2", "target": "n3", "target_handle": "right"},
             {"source": "n3", "target": "w0"}]
    _, out = run_cfg(ctx, [], nodes_edges=(nodes, edges))
    assert len(out) == sum(1 for r in ctx.rows if r["region"] in ("EU", "US")), f"{len(out)} rows"


@test("configure", "demo chain: filter -> aggregate -> sort (no LLM)")
def _(ctx: Ctx):
    t0 = time.time()
    _, out = run_cfg(ctx, [
        ("filter", {"conditions": [cond("order_total", ">", "50")]}),
        ("aggregate", {"group_by": ["region"], "metrics": [
            {"function": "sum", "column": "order_total", "alias": "total_revenue"},
            {"function": "avg", "column": "order_total", "alias": "avg_order_value"}]}),
        ("sort", {"sorts": [{"column": "total_revenue", "direction": "desc"}]}),
    ])
    kept = [r for r in ctx.rows if r["order_total"] > 50]
    exp = group_expect(kept, "region", "order_total", "sum")
    for r in out:
        assert close(num(r["total_revenue"]), exp[r["region"]]), f"{r['region']} revenue wrong"
    return f"{time.time() - t0:.1f}s end to end"


# ---- preflight schema endpoint ---------------------------------------------

@test("preflight", "predicts output columns without running")
def _(ctx: Ctx):
    nodes, edges = cfg_pipeline(ctx.require("csv_dataset"), [
        ("aggregate", {"group_by": ["region"], "metrics": [
            {"function": "sum", "column": "order_total", "alias": "total_revenue"}]}),
        ("predict_regression", {"target": "total_revenue"}),
    ])
    r = ctx.api.json("POST", "/pipelines/schema", {"nodes": nodes, "edges": edges})["nodes"]
    assert [c["name"] for c in r["n1"]["columns"]] == ["region", "total_revenue"], r["n1"]
    assert [c["name"] for c in r["n2"]["columns"]][-2:] == ["prediction", "split"], r["n2"]
    assert r["w0"]["columns"] == r["n2"]["columns"]


@test("preflight", "reports unknown columns per node")
def _(ctx: Ctx):
    nodes, edges = cfg_pipeline(ctx.require("csv_dataset"), [
        ("filter", {"conditions": [cond("no_such_column", "=", "x")]}),
    ])
    r = ctx.api.json("POST", "/pipelines/schema", {"nodes": nodes, "edges": edges})["nodes"]
    assert r["n1"]["error"] and "no_such_column" in r["n1"]["error"], r["n1"]
    assert r["w0"]["columns"] is None
    return r["n1"]["error"][:100]


@test("preflight", "prompt steps are unknown until run")
def _(ctx: Ctx):
    nodes, edges = linear(ctx.require("csv_dataset"), [("custom", "add a column")])
    r = ctx.api.json("POST", "/pipelines/schema", {"nodes": nodes, "edges": edges})["nodes"]
    assert r["s0"]["columns"] and r["n1"]["columns"] is None and r["n1"]["error"] is None, r


# ---- prediction --------------------------------------------------------------

def _ml_dataset(ctx: Ctx) -> str:
    if "ml_dataset" in ctx.state:
        return ctx.state["ml_dataset"]
    lines = ["x1,x2,segment,y,label"]
    for i in range(300):
        x1, x2 = (i * 37) % 101, (i * 13) % 17
        seg = ("a", "b", "c")[i % 3]
        y = 3 * x1 - 2 * x2 + (10 if seg == "a" else 0) + 5
        label = "high" if x1 + x2 > 60 else "low"
        lines.append(f"{x1},{x2},{seg},{y},{label}")
    ds = upload(ctx.api, "matrix_ml.csv", "csv", ("\n".join(lines) + "\n").encode())
    wait_dataset(ctx.api, ds, ctx.args.ingest_timeout)
    ctx.state["ml_dataset"] = ds
    return ds


def _ml_step(ctx: Ctx, kind: str, config: dict, dataset_id: str) -> tuple[dict, list[dict[str, str]]]:
    detail, out = run_cfg(ctx, [(kind, config)], dataset_id=dataset_id)
    ev = step_events(detail)["n1"]
    assert ev.get("metrics"), "no metrics in step_completed"
    assert ev.get("dataset_id"), "prediction output was not saved as a dataset"
    return ev["metrics"], out


@test("ml", "regression (linear) recovers the formula")
def _(ctx: Ctx):
    m, out = _ml_step(ctx, "predict_regression", {"target": "y", "features": ["x1", "x2", "segment"],
                                                  "model": "linear"}, _ml_dataset(ctx))
    assert m["r2"] > 0.999, f"r2={m['r2']}"
    assert {"prediction", "split"} <= headers_of(out)
    return f"r2={m['r2']} rmse={m['rmse']}"


@test("ml", "regression (random forest) on orders")
def _(ctx: Ctx):
    m, _ = _ml_step(ctx, "predict_regression", {"target": "order_total", "features": ["region", "order_status"]},
                    ctx.require("csv_dataset"))
    assert "r2" in m and m["importance"], m
    return f"r2={m['r2']}"


@test("ml", "classification")
def _(ctx: Ctx):
    m, out = _ml_step(ctx, "predict_class", {"target": "label", "features": ["x1", "x2"], "model": "decision_tree"},
                      _ml_dataset(ctx))
    assert m["accuracy"] > 0.9, f"accuracy={m['accuracy']}"
    assert all(0 <= num(r["probability"]) <= 1 for r in out)
    return f"accuracy={m['accuracy']} f1={m['f1']}"


@test("ml", "clustering")
def _(ctx: Ctx):
    m, out = _ml_step(ctx, "cluster", {"k": 3, "features": ["x1", "x2"]}, _ml_dataset(ctx))
    assert sum(m["sizes"].values()) == 300 and len(m["sizes"]) == 3, m["sizes"]
    assert {r["cluster"] for r in out} == {"0", "1", "2"}
    return f"silhouette={m['silhouette']}"


@test("ml", "anomaly detection")
def _(ctx: Ctx):
    m, out = _ml_step(ctx, "detect_anomalies", {"features": ["order_total"], "contamination": "0.05"},
                      ctx.require("csv_dataset"))
    flagged = sum(1 for r in out if r["is_anomaly"].lower() == "true")
    assert flagged == m["anomalies"] and 0 < flagged < len(out) * 0.1, f"{flagged} flagged"
    return f"{flagged} anomalies"


@test("ml", "monthly forecast")
def _(ctx: Ctx):
    m, out = _ml_step(ctx, "forecast", {"date_column": "order_date", "value_column": "order_total",
                                        "frequency": "M", "horizon": 3, "method": "linear_trend"},
                      ctx.require("csv_dataset"))
    fc = [r for r in out if r["series"] == "forecast"]
    assert len(fc) == 3 and m["history_periods"] == 12, f"{len(fc)} forecast rows, history={m['history_periods']}"
    return f"mape={m['mape_pct']}%"


@test("ml", "prediction ops ignore prompt mode (always configured)")
def _(ctx: Ctx):
    nodes, edges = linear(ctx.require("csv_dataset"), [("cluster", "")])
    nodes[1]["mode"] = "prompt"
    detail = wait_job(ctx.api, submit(ctx.api, nodes, edges, "auto"), ctx.args.job_timeout)
    # Python ops always run configured; empty config uses defaults (k=3, all usable columns).
    assert detail["status"] == "succeeded", f"{detail['status']}: {detail.get('error')}"


# ---- dashboard ---------------------------------------------------------------

def _dash_url(ctx: Ctx) -> str:
    if "dash_url" in ctx.state:
        return ctx.state["dash_url"]
    detail, _ = run_cfg(ctx, [("dashboard", {"title": "Matrix orders", "pie_column": "region",
                                             "line_x": "order_date", "line_y": "order_total"})])
    ev = step_events(detail)["n1"]
    assert ev.get("dashboard_url", "").startswith("/dash/?token="), f"dashboard_url={ev.get('dashboard_url')}"
    assert ev.get("dataset_id"), "dashboard data not saved"
    ctx.state["dash_url"] = ev["dashboard_url"]
    return ev["dashboard_url"]


@test("dashboard", "dashboard step emits a signed link")
def _(ctx: Ctx):
    return _dash_url(ctx)[:40] + "…"


@test("dashboard", "link opens without login; tampered link is 403")
def _(ctx: Ctx):
    url = ctx.api.base + _dash_url(ctx)
    status, _, body = ctx.api.request("GET", url, auth=False)
    assert status == 200 and b"_dash-config" in body, f"dashboard page -> {status}"
    assert b"/api/dash/" in body or b"/dash/" in body
    bad = url[:-3] + ("AAA" if not url.endswith("AAA") else "BBB")
    assert ctx.api.request("GET", bad, auth=False)[0] == 403, "tampered token accepted"
    assert ctx.api.request("GET", ctx.api.base + "/dash/", auth=False)[0] == 403, "missing token accepted"


@test("dashboard", "dashboard renders data (Dash callback)")
def _(ctx: Ctx):
    path = _dash_url(ctx)
    search = path[path.index("?"):]
    body = {
        "output": "page.children",
        "outputs": {"id": "page", "property": "children"},
        "inputs": [{"id": "url", "property": "search", "value": search}],
        "changedPropIds": ["url.search"],
    }
    status, _, payload = ctx.api.request("POST", ctx.api.base + "/dash/_dash-update-component", body=body, auth=False)
    assert status == 200, f"callback -> {status}: {payload[:200]!r}"
    text = payload.decode()
    assert "Matrix orders" in text and f"{len(ctx.rows):,}" in text, "title/row count missing"
    for chart in ("fig-histogram", "fig-pie", "fig-line", "table"):
        assert chart in text, f"{chart} missing"


# ---- scale (opt-in) -----------------------------------------------------------

def _scale_for(mb: int) -> None:
    @test("scale", f"{mb} MiB CSV: upload -> ingest -> auto pipeline")
    def _(ctx: Ctx):
        gen = os.path.join(REPO_ROOT, "scripts", "generate_test_csv.py")
        if not os.path.exists(gen):
            raise Skip(f"generator not found at {gen}")
        path = os.path.join(ctx.args.workdir, f"scale_{mb}mb.csv")
        if not os.path.exists(path):
            subprocess.run([sys.executable, gen, "--target-mb", str(mb), "--out", path], check=True,
                           stdout=subprocess.DEVNULL)
        with open(path, "rb") as fh:
            payload = fh.read()
        t0 = time.time()
        ds_id = upload(ctx.api, f"scale_{mb}mb.csv", "csv", payload)
        ds = wait_dataset(ctx.api, ds_id, ctx.args.ingest_timeout * 10)
        t_ingest = time.time() - t0
        nodes, edges = linear(ds_id, [
            ("filter", "Filter rows where department equals Engineering"),
            ("aggregate", "Sum of salary by region"),
        ])
        t1 = time.time()
        detail = wait_job(ctx.api, submit(ctx.api, nodes, edges, "auto"), ctx.args.job_timeout * 10)
        assert detail["status"] == "succeeded", f"{detail['status']}: {detail.get('error')}"
        return (f"rows={ds['row_count']} ingest={t_ingest:.0f}s pipeline={time.time() - t1:.0f}s "
                f"engine={detail['engine']}")


# ---- persistence (opt-in) ----------------------------------------------------

@test("persistence", "datasets and jobs survive a container restart")
def _(ctx: Ctx):
    if not ctx.args.container:
        raise Skip("pass --container NAME to test restart persistence")
    ds = ctx.require("csv_dataset")
    job = ctx.require("finished_job")
    subprocess.run(["docker", "restart", ctx.args.container], check=True, stdout=subprocess.DEVNULL)
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            if ctx.api.request("GET", "/ready", timeout=5)[0] == 200:
                break
        except Exception:
            pass
        time.sleep(2)
    else:
        raise AssertionError("not ready after restart")
    assert ctx.api.json("GET", f"/datasets/{ds}")["status"] == "ready", "dataset lost"
    assert ctx.api.json("GET", f"/jobs/{job}")["status"] == "succeeded", "job lost"
    assert len(export_rows(ctx.api, ds)) == len(ctx.rows), "dataset contents lost"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default="http://localhost:8090/api", help="API base URL (all-in-one: http://host:port/api)")
    p.add_argument("--ui", default=None, help="UI URL (defaults to API base without /api)")
    p.add_argument("--email", default="matrix@local")
    p.add_argument("--engines", default=",".join(ENGINES))
    p.add_argument("--only", default="", help=f"comma list of categories: {','.join(CATEGORIES)}")
    p.add_argument("--skip", default="", help="comma list of categories to skip")
    p.add_argument("--heavy-mb", default="", help="comma list of sizes for scale tests, e.g. 50,500")
    p.add_argument("--container", default="", help="container name for the restart/persistence test")
    p.add_argument("--job-timeout", type=float, default=180)
    p.add_argument("--llm-timeout", type=float, default=600)
    p.add_argument("--ingest-timeout", type=float, default=180)
    p.add_argument("--report", default="prompt-etl-test-report.json")
    p.add_argument("--workdir", default=os.path.join(tempfile.gettempdir(), "prompt-etl-matrix"))
    p.add_argument("--fail-fast", action="store_true")
    args = p.parse_args()
    os.makedirs(args.workdir, exist_ok=True)

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    for e in engines:
        _ops_for(e)
        _chains_for(e)
        _llm_for(e)
    for mb in [int(x) for x in args.heavy_mb.split(",") if x.strip()]:
        _scale_for(mb)

    only = {c for c in args.only.split(",") if c}
    skip = {c for c in args.skip.split(",") if c}
    if not args.heavy_mb:
        skip.add("scale")
    # Platform + ingest provide the datasets/flags every other category needs.
    selected = [t for t in TESTS if (not only or t[0] in only or t[0] in ("platform", "ingest")) and t[0] not in skip]
    # Persistence must run last: it restarts the container.
    selected.sort(key=lambda t: t[0] == "persistence")

    ctx = Ctx(api=Api(args.api, args.email, timeout=60), args=args, rows=build_rows())
    results: list[Result] = []
    colors = {"PASS": "\033[32m", "FAIL": "\033[31m", "SKIP": "\033[33m"}
    print(f"Prompt ETL test matrix -> {args.api}  ({len(selected)} checks, engines={','.join(engines)})\n")
    current = None
    for category, name, fn in selected:
        if category != current:
            current = category
            print(f"\033[1;36m== {category}\033[0m")
        t0 = time.time()
        try:
            detail = fn(ctx) or ""
            status = "PASS"
        except Skip as e:
            status, detail = "SKIP", str(e)
        except AssertionError as e:
            status, detail = "FAIL", str(e) or "assertion failed"
        except Exception as e:  # infrastructure / API errors
            status, detail = "FAIL", f"{type(e).__name__}: {e}"
            if os.environ.get("MATRIX_DEBUG"):
                traceback.print_exc()
        dt = time.time() - t0
        results.append(Result(category, name, status, round(dt, 2), detail))
        print(f"  {colors[status]}{status}\033[0m {name} ({dt:.1f}s){'  - ' + detail if detail else ''}")
        if status == "FAIL" and args.fail_fast:
            break

    counts = {s: sum(1 for r in results if r.status == s) for s in ("PASS", "FAIL", "SKIP")}
    print(f"\n\033[1mPASS={counts['PASS']} FAIL={counts['FAIL']} SKIP={counts['SKIP']}\033[0m")
    report = {
        "api": args.api,
        "engines": engines,
        "ollama": ctx.state.get("ollama"),
        "counts": counts,
        "results": [r.__dict__ for r in results],
    }
    with open(args.report, "w") as fh:
        json.dump(report, fh, indent=2)
    print(f"report: {os.path.abspath(args.report)}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
