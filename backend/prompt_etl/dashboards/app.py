"""The dashboard page: a server-paged table plus histogram, pie and line charts.

Mounted by FastAPI at ``/dash`` (``/api/dash/`` behind nginx). Every data
callback re-checks the signed token from the page URL.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from collections import OrderedDict
from typing import Any
from urllib.parse import parse_qs

import pandas as pd

from ..config import get_settings
from ..services.object_store import get_object_store
from .tokens import read_token

CHART_SAMPLE_ROWS = 200_000
_CACHE_MAX = 4
_cache: "OrderedDict[str, pd.DataFrame]" = OrderedDict()
_cache_lock = threading.Lock()

_BG = "#0b0d17"
_CARD = "#141827"
_TEXT = "#e5e7f0"
_MUTED = "#8b90a8"
_ACCENT = ["#8b5cf6", "#22d3ee", "#f59e0b", "#10b981", "#ef4444", "#3b82f6", "#ec4899", "#84cc16"]


def _load(object_key: str) -> pd.DataFrame:
    with _cache_lock:
        if object_key in _cache:
            _cache.move_to_end(object_key)
            return _cache[object_key]
    cache_dir = os.path.join(tempfile.gettempdir(), "prompt-etl-dash")
    os.makedirs(cache_dir, exist_ok=True)
    local = os.path.join(cache_dir, hashlib.sha256(object_key.encode()).hexdigest() + ".parquet")
    if not os.path.exists(local):
        tmp = local + ".part"
        get_object_store().download_to_file(object_key, tmp)
        os.replace(tmp, local)
    df = pd.read_parquet(local)
    with _cache_lock:
        _cache[object_key] = df
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return df


def _token_from_search(search: str | None) -> dict[str, Any] | None:
    qs = parse_qs((search or "").lstrip("?"))
    return read_token((qs.get("token") or [None])[0])


def _pick(df: pd.DataFrame, wanted: Any, test) -> str | None:
    if isinstance(wanted, str) and wanted in df.columns:
        return wanted
    for c in df.columns:
        if test(df[c]):
            return c
    return None


def _is_num(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def _is_category(s: pd.Series) -> bool:
    return (not _is_num(s)) and not _is_dateish(s) and 1 < s.nunique(dropna=True) <= 30


def _is_dateish(s: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(s):
        return True
    return s.dtype == object and pd.api.types.infer_dtype(s, skipna=True) in {"date", "datetime"}


def _style_fig(fig: Any, title: str) -> Any:
    fig.update_layout(
        title={"text": title, "font": {"size": 14}},
        paper_bgcolor=_CARD, plot_bgcolor=_CARD, font={"color": _TEXT},
        margin={"l": 40, "r": 20, "t": 50, "b": 40}, colorway=_ACCENT, height=340,
        legend={"bgcolor": "rgba(0,0,0,0)"},
    )
    fig.update_xaxes(gridcolor="#23283b")
    fig.update_yaxes(gridcolor="#23283b")
    return fig


def build_figures(df: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    import plotly.express as px
    import plotly.graph_objects as go

    data = df.sample(CHART_SAMPLE_ROWS, random_state=0) if len(df) > CHART_SAMPLE_ROWS else df
    figs: dict[str, Any] = {}

    hist_col = _pick(data, cfg.get("histogram_column"), _is_num)
    if hist_col:
        figs["histogram"] = _style_fig(px.histogram(data, x=hist_col, nbins=40), f"Distribution of {hist_col}")
    else:
        figs["histogram"] = _style_fig(go.Figure(), "Histogram: no numeric column")

    pie_col = _pick(data, cfg.get("pie_column"), _is_category)
    if pie_col:
        val = cfg.get("pie_value") if cfg.get("pie_value") in data.columns else None
        if val and _is_num(data[val]):
            agg = data.groupby(pie_col, dropna=False)[val].sum().sort_values(ascending=False)
            title = f"{val} by {pie_col}"
        else:
            agg = data[pie_col].value_counts(dropna=False)
            title = f"Rows by {pie_col}"
        if len(agg) > 10:
            agg = pd.concat([agg.iloc[:9], pd.Series({"Other": agg.iloc[9:].sum()})])
        pie = px.pie(names=[str(i) for i in agg.index], values=agg.values, hole=0.45)
        figs["pie"] = _style_fig(pie, title)
    else:
        figs["pie"] = _style_fig(go.Figure(), "Pie: no low-cardinality column")

    x = _pick(data, cfg.get("line_x"), _is_dateish)
    y = _pick(data, cfg.get("line_y") or cfg.get("histogram_column"), _is_num)
    group = cfg.get("line_group") if cfg.get("line_group") in data.columns else None
    if x and y:
        keys = [x] + ([group] if group else [])
        series = data.groupby(keys, dropna=True)[y].sum().reset_index().sort_values(x)
        line = px.line(series, x=x, y=y, color=group, markers=len(series) < 60)
        figs["line"] = _style_fig(line, f"{y} over {x}" + (f" by {group}" if group else ""))
    elif y:
        line = px.line(data.reset_index(drop=True), y=y)
        figs["line"] = _style_fig(line, f"{y} by row")
    else:
        figs["line"] = _style_fig(go.Figure(), "Line: no numeric column")
    return figs


_FILTER_OPS = [
    ("ge ", ">="), ("le ", "<="), ("lt ", "<"), ("gt ", ">"), ("ne ", "!="), ("eq ", "="),
    ("contains ", "contains"), ("datestartswith ", "datestartswith"),
]


def _split_filter(part: str) -> tuple[str | None, str | None, Any]:
    for token, op in _FILTER_OPS:
        if token in part:
            name, value = part.split(token, 1)
            name = name[name.find("{") + 1: name.rfind("}")]
            value = value.strip()
            if value and value[0] == value[-1] and value[0] in "'\"`":
                value = value[1:-1].replace("\\" + value[0], value[0])
            else:
                try:
                    value = float(value)
                except ValueError:
                    pass
            return name, op, value
    return None, None, None


def apply_table_query(df: pd.DataFrame, filter_query: str, sort_by: list[dict[str, str]]) -> pd.DataFrame:
    out = df
    for part in (filter_query or "").split(" && "):
        col, op, value = _split_filter(part)
        if not col or col not in out.columns:
            continue
        s = out[col]
        if op == "contains":
            out = out[s.astype(str).str.contains(str(value), case=False, na=False, regex=False)]
        elif op == "datestartswith":
            out = out[s.astype(str).str.startswith(str(value))]
        else:
            try:
                cmp = {"=": s.__eq__, "!=": s.__ne__, ">": s.__gt__, ">=": s.__ge__,
                       "<": s.__lt__, "<=": s.__le__}[op](value)
                out = out[cmp.fillna(False)]
            except TypeError:
                out = out[s.astype(str) == str(value)]
    if sort_by:
        out = out.sort_values(
            [s["column_id"] for s in sort_by if s["column_id"] in out.columns],
            ascending=[s["direction"] == "asc" for s in sort_by if s["column_id"] in out.columns],
            na_position="last",
        )
    return out


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    safe = df.copy()
    for c in safe.columns:
        if not (_is_num(safe[c]) or pd.api.types.is_bool_dtype(safe[c])):
            safe[c] = safe[c].astype(str).where(safe[c].notna(), None)
    return safe.astype(object).where(safe.notna(), None).to_dict("records")


def _kpi(label: str, value: str) -> Any:
    from dash import html

    return html.Div([
        html.Div(label, style={"color": _MUTED, "fontSize": "11px", "textTransform": "uppercase",
                               "letterSpacing": "0.12em"}),
        html.Div(value, style={"fontSize": "22px", "fontWeight": 600, "marginTop": "4px"}),
    ], style={"background": _CARD, "borderRadius": "12px", "padding": "14px 18px", "flex": "1",
              "minWidth": "150px", "border": "1px solid #23283b"})


def _page(payload: dict[str, Any]) -> Any:
    from dash import dash_table, dcc, html

    df = _load(payload["k"])
    cfg = payload.get("c") or {}
    figs = build_figures(df, cfg)
    page_size = int(cfg.get("page_size") or 20)
    num_cols = [c for c in df.columns if _is_num(df[c])]
    kpis = [_kpi("Rows", f"{len(df):,}"), _kpi("Columns", str(len(df.columns)))]
    for c in num_cols[:2]:
        kpis.append(_kpi(f"Total {c}", f"{df[c].sum():,.2f}"))
    card = {"background": _CARD, "borderRadius": "12px", "padding": "8px", "border": "1px solid #23283b"}
    graph_cfg = {"displaylogo": False, "responsive": True}
    return html.Div([
        html.Div([
            html.H1(payload.get("t") or "Dashboard", style={"margin": 0, "fontSize": "22px"}),
            html.Div("Prompt ETL dashboard" + (f" · charts use a {CHART_SAMPLE_ROWS:,}-row sample"
                                               if len(df) > CHART_SAMPLE_ROWS else ""),
                     style={"color": _MUTED, "fontSize": "12px", "marginTop": "4px"}),
        ], style={"marginBottom": "16px"}),
        html.Div(kpis, style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginBottom": "16px"}),
        html.Div([
            html.Div(dcc.Graph(id="fig-histogram", figure=figs["histogram"], config=graph_cfg), style=card),
            html.Div(dcc.Graph(id="fig-pie", figure=figs["pie"], config=graph_cfg), style=card),
        ], style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(420px, 1fr))",
                  "gap": "16px", "marginBottom": "16px"}),
        html.Div(dcc.Graph(id="fig-line", figure=figs["line"], config=graph_cfg),
                 style={**card, "marginBottom": "16px"}),
        html.Div([
            dash_table.DataTable(
                id="table",
                columns=[{"name": str(c), "id": str(c)} for c in df.columns],
                page_current=0, page_size=page_size, page_action="custom",
                sort_action="custom", sort_mode="multi", sort_by=[],
                filter_action="custom", filter_query="",
                style_table={"overflowX": "auto"},
                style_header={"backgroundColor": "#1c2135", "color": _TEXT, "fontWeight": 600,
                              "border": "1px solid #23283b"},
                style_cell={"backgroundColor": _CARD, "color": _TEXT, "border": "1px solid #23283b",
                            "fontFamily": "ui-monospace, monospace", "fontSize": "12px",
                            "padding": "6px 10px", "maxWidth": "320px", "overflow": "hidden",
                            "textOverflow": "ellipsis"},
                style_filter={"backgroundColor": "#10131f", "color": _TEXT},
            ),
            html.Div(id="table-count", style={"color": _MUTED, "fontSize": "12px", "marginTop": "8px"}),
        ], style={**card, "padding": "14px"}),
    ])


def create_dash_app() -> Any:
    from dash import Dash, Input, Output, State, dcc, html
    from flask import abort, request

    prefix = get_settings().dash_path_prefix
    app = Dash(
        __name__,
        requests_pathname_prefix=prefix,
        routes_pathname_prefix="/",
        title="Prompt ETL dashboard",
        suppress_callback_exceptions=True,
        update_title=None,
    )
    app.layout = html.Div(
        [dcc.Location(id="url"), html.Div(id="page")],
        style={"background": _BG, "color": _TEXT, "minHeight": "100vh", "padding": "24px",
               "fontFamily": "Inter, system-ui, -apple-system, sans-serif"},
    )

    @app.server.before_request
    def _require_token() -> None:
        if request.path == "/" and read_token(request.args.get("token")) is None:
            abort(403, "This dashboard link is invalid or has expired. Re-run the pipeline for a new link.")

    @app.callback(Output("page", "children"), Input("url", "search"))
    def _render(search: str | None) -> Any:
        payload = _token_from_search(search)
        if payload is None:
            return html.Div("This dashboard link is invalid or has expired.", style={"color": "#f87171"})
        try:
            return _page(payload)
        except Exception as e:  # noqa: BLE001 - shown to the user instead of a blank page
            return html.Div(f"Could not load the data: {e}", style={"color": "#f87171"})

    @app.callback(
        Output("table", "data"),
        Output("table", "page_count"),
        Output("table-count", "children"),
        Input("table", "page_current"),
        Input("table", "page_size"),
        Input("table", "sort_by"),
        Input("table", "filter_query"),
        State("url", "search"),
    )
    def _table(page: int, size: int, sort_by: list, filter_query: str, search: str | None):
        payload = _token_from_search(search)
        if payload is None:
            return [], 0, "Invalid or expired link."
        view = apply_table_query(_load(payload["k"]), filter_query, sort_by or [])
        size = size or 20
        rows = view.iloc[(page or 0) * size: ((page or 0) + 1) * size]
        return _records(rows), max(1, -(-len(view) // size)), f"{len(view):,} matching rows"

    return app
