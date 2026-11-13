"""Dashboard box: passes data through; the pipeline publishes a Dash page for it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import OpSpec, Param, PyResult, Schema
from .registry import register

if TYPE_CHECKING:
    import pandas as pd

DASHBOARD_OP = "dashboard"


def _passthrough(dfs: list["pd.DataFrame"], cfg: dict[str, Any]) -> PyResult:
    return PyResult(dfs[0], {"task": "dashboard", "rows": int(len(dfs[0]))})


def _same_schema(cfg: dict[str, Any], schemas: list[Schema]) -> Schema:
    return schemas[0]


register(OpSpec(
    id=DASHBOARD_OP, label="Dashboard", category="visualize", engine="python",
    description="Table, histogram, pie and line chart for this data; opens in a new tab",
    params=(
        Param("title", "Title", "text", placeholder="Orders overview"),
        Param("histogram_column", "Histogram of", "column", help="Empty = first numeric column"),
        Param("pie_column", "Pie chart by", "column", help="Empty = first text column with few values"),
        Param("pie_value", "Pie slice size", "column", help="Empty = count rows"),
        Param("line_x", "Line chart x (date or order)", "column", help="Empty = first date column"),
        Param("line_y", "Line chart y", "column", help="Empty = histogram column"),
        Param("line_group", "One line per", "column"),
        Param("page_size", "Table rows per page", "integer", default=20),
    ),
    run=_passthrough, output_schema=_same_schema, persist_output=True,
))
