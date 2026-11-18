"""AIECC FastAPI application factory."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.api.middleware import RequestContextMiddleware
from app.api.v1 import (
    agents,
    auth,
    engineering,
    evaluations,
    experiments,
    knowledge,
    mcp,
    platform,
    prompts,
    providers,
    security,
    workflows,
)
from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis, init_redis
from app.observability.metrics import prometheus_text
from app.observability.tracing import configure_tracing

log = get_logger("aiecc")


async def _knowledge_search(org_id: str, query: str, top_k: int, collection: str | None) -> list[dict[str, Any]]:
    from app.rag import service as rag

    async with get_sessionmaker()() as db:
        # read-only nested session: usage is attributed to the calling agent execution, not written here
        res = await rag.search(db, org_id=org_id, query=query, strategy="hybrid_rerank", top_k=top_k,
                               collection=collection, persist_usage=False)
        await db.rollback()
        return res["hits"]


async def wire_integrations() -> None:
    from app.integrations.tools.builtin import BuiltinToolAdapter

    BuiltinToolAdapter.knowledge_search = _knowledge_search


async def _warm_mcp_pool() -> None:
    """Pre-connect enabled STDIO/HTTP MCP servers that were connected before (avoids cold-start on first call)."""
    from sqlalchemy import select

    from app.integrations.mcp.manager import mcp_manager
    from app.models import McpServer

    async with get_sessionmaker()() as db:
        servers = (await db.execute(select(McpServer).where(McpServer.is_enabled.is_(True),
                                                            McpServer.transport != "builtin"))).scalars().all()
    for srv in servers:
        try:
            await mcp_manager.connect(srv)
        except Exception as exc:  # noqa: BLE001
            log.warning("mcp_warmup_failed server=%s: %s", srv.name, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    configure_tracing(app)
    await init_redis()
    await wire_integrations()
    if s.is_sqlite and s.env == "local":
        # No-Docker quickstart mode (ADR-0003): Alembic migrations target PostgreSQL/pgvector, so the
        # SQLite fallback creates the schema from metadata. Never used for staging/production.
        from app.core.db import Base, get_engine

        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.warning("sqlite_quickstart_mode: schema created from metadata; use PostgreSQL for real deployments")
    if s.seed_demo_data and s.env != "production":
        from app.seed.demo import seed_if_empty

        try:
            await seed_if_empty()
        except Exception:  # noqa: BLE001 - never block startup on demo data
            log.exception("demo_seed_failed")
    warm = asyncio.create_task(_warm_mcp_pool()) if s.env != "test" else None
    log.info("aiecc_started env=%s db=%s", s.env, s.database_url.split("@")[-1])
    yield
    if warm and not warm.done():
        warm.cancel()
    # ---- graceful shutdown: stop intake -> drain work -> close pools ---------------------------
    from app.agents import runtime
    from app.core import jobs
    from app.integrations.llm.gateway import gateway
    from app.integrations.mcp.manager import mcp_manager

    log.info("aiecc_shutdown_begin")
    await runtime.shutdown(grace_s=10)
    await jobs.drain(timeout=10)
    await mcp_manager.shutdown_all()
    await gateway.aclose()
    await close_redis()
    await dispose_engine()
    log.info("aiecc_shutdown_complete")


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="AI Engineering Command Center (AIECC) API",
        version="1.0.0",
        description="Enterprise AI engineering platform: agents, MCP, prompts, RAG, evaluation, red teaming, "
                    "experiments, workflows, observability, security and audit.",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    register_exception_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID", "X-API-Key"])
    app.add_middleware(RequestContextMiddleware)
    p = s.api_prefix
    for r in (auth.router, security.router, agents.router, agents.executions_router, providers.router, mcp.router,
              mcp.tools_router, prompts.router, knowledge.router, evaluations.router, experiments.router,
              workflows.router, workflows.wf_exec_router, engineering.router, platform.router):
        app.include_router(r, prefix=p)
    app.include_router(platform.health_router)

    @app.get("/metrics", include_in_schema=False)
    async def prom() -> Response:
        # Internal scrape endpoint; nginx does not expose it publicly (see infrastructure/nginx).
        return Response(prometheus_text(), media_type="text/plain; version=0.0.4")

    return app


app = create_app()
