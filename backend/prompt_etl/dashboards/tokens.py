"""Signed, expiring dashboard links.

A dashboard opens in a new browser tab, which can't send the API's auth
header, so the link itself carries an HMAC-signed token naming the dataset
object and the dashboard settings.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from ..config import get_settings

TOKEN_TTL_SECONDS = 7 * 24 * 3600


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    key = get_settings().secret_key.encode()
    return _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())


def make_token(*, object_key: str, config: dict[str, Any], title: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    payload = {"k": object_key, "c": config, "t": title, "e": int(time.time()) + ttl}
    body = _b64(json.dumps(payload, separators=(",", ":"), default=str).encode())
    return f"{body}.{_sign(body)}"


def read_token(token: str | None) -> dict[str, Any] | None:
    """Payload for a valid, unexpired token; otherwise None."""
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("e", 0)) < time.time():
        return None
    return payload


def dashboard_path(*, object_key: str, config: dict[str, Any], title: str) -> str:
    """Path relative to the API base, e.g. ``/dash/?token=...``."""
    return f"/dash/?token={make_token(object_key=object_key, config=config, title=title)}"
