"""The operation catalog."""

from __future__ import annotations

from typing import Any

from .base import OpSpec

CATEGORIES: list[tuple[str, str]] = [
    ("columns", "Columns"),
    ("rows", "Rows"),
    ("text", "Text"),
    ("dates", "Dates"),
    ("combine", "Combine"),
    ("reshape", "Reshape"),
    ("quality", "Data quality"),
    ("predict", "Prediction"),
    ("visualize", "Visualize"),
    ("custom", "Custom"),
]

REGISTRY: dict[str, OpSpec] = {}
_loaded = False


def register(spec: OpSpec) -> OpSpec:
    if spec.id in REGISTRY:
        raise ValueError(f"duplicate op id {spec.id}")
    REGISTRY[spec.id] = spec
    return spec


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    from . import (  # noqa: F401
        columns,
        combine,
        custom,
        dashboard,
        dates,
        ml,
        quality,
        reshape,
        rows,
        text,
    )


def get_op(op_id: str) -> OpSpec | None:
    _load()
    return REGISTRY.get(op_id)


def all_ops() -> list[OpSpec]:
    _load()
    order = {c: i for i, (c, _) in enumerate(CATEGORIES)}
    return sorted(REGISTRY.values(), key=lambda s: order.get(s.category, 99))


def catalog_json() -> dict[str, Any]:
    return {
        "categories": [{"id": c, "label": label} for c, label in CATEGORIES],
        "operations": [s.to_json() for s in all_ops()],
    }
