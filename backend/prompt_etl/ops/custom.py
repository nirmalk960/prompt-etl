"""The free-form prompt box (LLM only)."""

from __future__ import annotations

from .base import OpSpec
from .registry import register

register(OpSpec(
    id="custom", label="Custom (prompt)", category="custom", engine="prompt",
    description="Describe any transform in plain English; the local LLM writes the code",
))
