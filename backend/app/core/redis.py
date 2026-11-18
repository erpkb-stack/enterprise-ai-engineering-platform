"""Redis connectivity with graceful degradation to in-process fallbacks.

Redis backs rate limiting, idempotency keys, response caching, and the ARQ job queue.
When Redis is unavailable (e.g. unit tests) we fall back to in-memory structures and
report the degraded state on /health so operators can see it.
"""

from __future__ import annotations

import time

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_client: Redis | None = None
_available: bool = False


async def init_redis() -> bool:
    global _client, _available
    url = get_settings().redis_url
    if not url:
        _available = False
        return False
    try:
        _client = Redis.from_url(url, decode_responses=True, socket_connect_timeout=1.5, socket_timeout=2.0)
        await _client.ping()
        _available = True
    except Exception as exc:  # noqa: BLE001 - degrade, don't crash
        log.warning("redis_unavailable_falling_back_to_memory: %s", exc)
        _client, _available = None, False
    return _available


async def close_redis() -> None:
    global _client, _available
    if _client is not None:
        await _client.aclose()
    _client, _available = None, False


def redis_client() -> Redis | None:
    return _client if _available else None


async def redis_ping() -> dict:
    c = redis_client()
    if c is None:
        return {"status": "degraded", "detail": "redis not configured or unreachable; using in-memory fallback"}
    t0 = time.perf_counter()
    try:
        await c.ping()
        return {"status": "ok", "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc)}


class MemoryTTLStore:
    """Tiny TTL key-value store used when Redis is unavailable."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        item = self._data.get(key)
        if not item:
            return None
        exp, val = item
        if exp < time.monotonic():
            self._data.pop(key, None)
            return None
        return val

    def set(self, key: str, value: str, ttl_s: float) -> None:
        if len(self._data) > 50_000:  # bound memory
            now = time.monotonic()
            self._data = {k: v for k, v in self._data.items() if v[0] > now}
        self._data[key] = (time.monotonic() + ttl_s, value)

    def incr(self, key: str, ttl_s: float) -> int:
        cur = self.get(key)
        n = int(cur) + 1 if cur else 1
        exp = self._data[key][0] if cur else time.monotonic() + ttl_s
        self._data[key] = (exp, str(n))
        return n


memory_store = MemoryTTLStore()


async def kv_get(key: str) -> str | None:
    c = redis_client()
    if c is not None:
        try:
            return await c.get(key)
        except Exception as exc:  # noqa: BLE001 - fall back to in-memory store
            log.debug("redis_op_failed_fallback: %s", exc)
    return memory_store.get(key)


async def kv_set(key: str, value: str, ttl_s: int) -> None:
    c = redis_client()
    if c is not None:
        try:
            await c.set(key, value, ex=ttl_s)
            return
        except Exception as exc:  # noqa: BLE001 - fall back to in-memory store
            log.debug("redis_op_failed_fallback: %s", exc)
    memory_store.set(key, value, ttl_s)


async def kv_incr(key: str, ttl_s: int) -> int:
    c = redis_client()
    if c is not None:
        try:
            pipe = c.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl_s, nx=True)
            n, _ = await pipe.execute()
            return int(n)
        except Exception as exc:  # noqa: BLE001 - fall back to in-memory store
            log.debug("redis_op_failed_fallback: %s", exc)
    return memory_store.incr(key, ttl_s)
