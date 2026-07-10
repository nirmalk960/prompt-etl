"""Thin async client for Ollama's /api/generate endpoint.

We do NOT use the official `ollama` python client because:
  - it's sync-only
  - it pulls in heavy deps
A simple HTTPX call is enough and easy to stub for tests.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings

log = logging.getLogger(__name__)


class OllamaUnavailable(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, *, base_url: str | None = None, model: str | None = None):
        s = get_settings()
        self.base_url = (base_url or s.ollama_base_url).rstrip("/")
        self.model = model or s.ollama_model
        self.disabled = s.ollama_disable

    async def is_available(self) -> bool:
        if self.disabled:
            return False
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"{self.base_url}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    async def generate(
        self,
        *,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        stop: list[str] | None = None,
    ) -> str:
        if self.disabled:
            raise OllamaUnavailable("Ollama is disabled by config (OLLAMA_DISABLE=1)")
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if system:
            payload["system"] = system
        if stop:
            payload["options"]["stop"] = stop
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(f"{self.base_url}/api/generate", json=payload)
            if r.status_code != 200:
                raise OllamaUnavailable(
                    f"Ollama returned {r.status_code}: {r.text[:200]}"
                )
            data = r.json()
            return data.get("response", "").strip()
