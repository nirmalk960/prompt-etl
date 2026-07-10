"""Process-wide Arq enqueue pool, separate from the worker's pool."""

from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from .config import get_settings

_pool: ArqRedis | None = None


async def get_arq_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool


async def shutdown_arq_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
    _pool = None
