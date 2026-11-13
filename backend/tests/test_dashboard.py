"""Dashboard tokens, chart building and the token gate."""

from __future__ import annotations

import pandas as pd

from prompt_etl.dashboards import tokens
from prompt_etl.dashboards.app import apply_table_query, build_figures, create_dash_app


def test_token_roundtrip_and_tamper() -> None:
    tok = tokens.make_token(object_key="ws/a.parquet", config={"title": "x"}, title="T")
    payload = tokens.read_token(tok)
    assert payload is not None and payload["k"] == "ws/a.parquet"
    body, sig = tok.rsplit(".", 1)
    forged = tokens._b64(b'{"k":"other/b.parquet","c":{},"t":"","e":9999999999}')
    assert tokens.read_token(f"{forged}.{sig}") is None
    assert tokens.read_token(body + ".x") is None
    assert tokens.read_token(None) is None


def test_token_expiry() -> None:
    tok = tokens.make_token(object_key="k", config={}, title="t", ttl=-1)
    assert tokens.read_token(tok) is None


def _orders() -> pd.DataFrame:
    return pd.DataFrame({
        "order_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-02", "2024-01-03"]),
        "region": ["north", "south", "north", "east"],
        "total": [10.0, 20.0, 30.0, 40.0],
    })


def test_figures_auto_pick_columns() -> None:
    figs = build_figures(_orders(), {})
    assert "total" in figs["histogram"].layout.title.text
    assert figs["pie"].layout.title.text == "Rows by region"
    assert figs["line"].layout.title.text == "total over order_date"


def test_figures_respect_config() -> None:
    figs = build_figures(_orders(), {"pie_column": "region", "pie_value": "total", "line_group": "region"})
    assert figs["pie"].layout.title.text == "total by region"
    assert len(figs["line"].data) == 3


def test_table_query_filter_and_sort() -> None:
    out = apply_table_query(_orders(), "{total} ge 20 && {region} contains north", [])
    assert out["total"].tolist() == [30.0]
    out = apply_table_query(_orders(), "", [{"column_id": "total", "direction": "desc"}])
    assert out["total"].tolist()[0] == 40.0


def test_index_requires_token() -> None:
    client = create_dash_app().server.test_client()
    assert client.get("/").status_code == 403
    assert client.get("/?token=bad.token").status_code == 403
    tok = tokens.make_token(object_key="k", config={}, title="t")
    assert client.get(f"/?token={tok}").status_code == 200
