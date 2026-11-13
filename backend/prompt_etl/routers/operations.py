"""The operation catalog that drives the canvas palette and settings forms."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..ops import catalog_json

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("")
async def list_operations() -> dict[str, Any]:
    return catalog_json()
