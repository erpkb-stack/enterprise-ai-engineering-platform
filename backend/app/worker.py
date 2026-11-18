"""ARQ worker entrypoint: `arq app.worker.WorkerSettings`."""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.ids import utcnow
from app.core.jobs import principal_from
from app.core.logging import configure_logging


async def run_evaluation(ctx: Any, run_id: str, principal: dict) -> None:
    from app.evaluations.engine import execute_run

    await execute_run(run_id, principal_from(principal))


async def run_redteam(ctx: Any, run_id: str, provider: str, model: str, scenario_ids: list[str] | None, principal: dict) -> None:
    from app.evaluations import redteam
    from app.models import EvaluationRun
    from app.security import audit

    p = principal_from(principal)
    async with get_sessionmaker()() as db:
        run = (await db.execute(select(EvaluationRun).where(EvaluationRun.id == run_id))).scalar_one()
        run.status = "running"
        await db.commit()
        try:
            summary = await redteam.run_suite(db, org=run.organization_id, principal=p, provider=provider, model=model,
                                              scenario_ids=scenario_ids)
            run.summary, run.status = summary, "completed"
        except Exception as exc:  # noqa: BLE001
            run.status, run.error = "failed", str(exc)[:1000]
        run.finished_at = utcnow()
        await audit.record(db, org_id=run.organization_id, action="redteam.run", category="evaluation", actor_id=p.user_id,
                           actor_email=p.email, resource_type="evaluation_run", resource_id=run.id,
                           outcome="success" if run.status == "completed" else "failure",
                           severity="warning" if (run.summary or {}).get("defense_rate", 1) not in (None, 1, 1.0) else "info",
                           details={"defense_rate": (run.summary or {}).get("defense_rate"), "provider": provider})
        await db.commit()


JOBS = {"run_evaluation": run_evaluation, "run_redteam": run_redteam}


async def _startup(ctx: Any) -> None:
    configure_logging(get_settings().log_level, get_settings().log_json)
    from app.main import wire_integrations

    await wire_integrations()


class WorkerSettings:
    functions = list(JOBS.values())
    on_startup = _startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379/0")
    max_jobs = 8
    job_timeout = 1800
