"""Background job dispatch.

  inline (default for local dev/tests): asyncio tasks tracked in-process, drained on shutdown
  arq: jobs are enqueued to Redis and executed by `arq app.worker.WorkerSettings`
Job functions take only JSON-serialisable args (principal is passed as a dict).
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.security.principal import Principal

log = get_logger(__name__)
_tasks: set[asyncio.Task] = set()
_arq_pool = None


async def _dispatch(name: str, kwargs: dict[str, Any]) -> None:
    from app.worker import JOBS

    await JOBS[name](None, **kwargs)


async def enqueue(name: str, principal: Principal, **kwargs: Any) -> str:
    global _arq_pool
    payload = {**kwargs, "principal": {**asdict(principal), "permissions": sorted(principal.permissions)}}
    if get_settings().jobs_mode == "arq":
        from arq import create_pool
        from arq.connections import RedisSettings

        if _arq_pool is None:
            _arq_pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
        job = await _arq_pool.enqueue_job(name, **payload)
        return job.job_id if job else "duplicate"
    t = asyncio.create_task(_dispatch(name, payload), name=f"job-{name}")
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    t.add_done_callback(lambda tt: tt.exception() and log.error("job_failed %s: %s", name, tt.exception()))
    return t.get_name()


def principal_from(d: dict[str, Any]) -> Principal:
    return Principal(**{**d, "permissions": set(d.get("permissions", []))})


async def drain(timeout: float = 10.0) -> None:
    if _tasks:
        await asyncio.wait(list(_tasks), timeout=timeout)


def pending() -> int:
    return len(_tasks)
