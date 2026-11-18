"""Dashboard overview, performance metrics, load-test runner, settings, WebSocket event stream."""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import runtime
from app.api.middleware import synthetic_token
from app.core import jobs
from app.core.config import get_settings
from app.core.db import get_db, get_sessionmaker
from app.core.errors import FeatureDisabledError
from app.core.ids import utcnow
from app.core.redis import redis_ping
from app.core.resilience import breakers
from app.integrations.mcp.manager import mcp_manager
from app.models import (
    Agent,
    AgentExecution,
    AuditEvent,
    EvaluationRun,
    McpServer,
    McpTool,
    ToolExecution,
    UsageMetric,
    User,
    WorkflowExecution,
)
from app.observability import metrics
from app.observability.events import bus, encode
from app.security import audit
from app.security.crypto import decode_access_token
from app.security.principal import Principal, require

router = APIRouter(tags=["platform"])


@router.get("/dashboard/overview", summary="Live overview computed from persisted executions and measured telemetry")
async def overview(hours: int = Query(24, ge=1, le=720), db: AsyncSession = Depends(get_db),
                   p: Principal = Depends(require("metrics:read"))) -> dict:
    since = utcnow() - timedelta(hours=hours)
    org = p.org_id
    agents = dict((await db.execute(select(Agent.status, func.count()).where(Agent.organization_id == org)
                                    .group_by(Agent.status))).all())
    servers = (await db.execute(select(McpServer).where(McpServer.organization_id == org))).scalars().all()
    tools_enabled = (await db.execute(select(func.count()).select_from(McpTool).where(McpTool.organization_id == org,
                                                                                      McpTool.is_enabled.is_(True)))).scalar_one()
    ex_rows = (await db.execute(select(AgentExecution.status, func.count(), func.avg(AgentExecution.latency_ms))
                                .where(AgentExecution.organization_id == org, AgentExecution.created_at >= since)
                                .group_by(AgentExecution.status))).all()
    ex_total = sum(n for _, n, _ in ex_rows)
    ex_failed = sum(n for s, n, _ in ex_rows if s in ("failed", "timeout", "max_steps_exceeded"))
    wf = dict((await db.execute(select(WorkflowExecution.status, func.count()).where(WorkflowExecution.organization_id == org,
                                                                                    WorkflowExecution.created_at >= since)
                                .group_by(WorkflowExecution.status))).all())
    usage = (await db.execute(select(UsageMetric.provider, UsageMetric.model, func.count(), func.sum(UsageMetric.tokens_in),
                                     func.sum(UsageMetric.tokens_out), func.sum(UsageMetric.cost_usd),
                                     func.avg(UsageMetric.latency_ms),
                                     func.sum(case((UsageMetric.success.is_(False), 1), else_=0)),
                                     func.max(case((UsageMetric.is_mock.is_(True), 1), else_=0)))
                              .where(UsageMetric.organization_id == org, UsageMetric.kind == "llm", UsageMetric.created_at >= since)
                              .group_by(UsageMetric.provider, UsageMetric.model))).all()
    last_eval = (await db.execute(select(EvaluationRun).where(EvaluationRun.organization_id == org,
                                                              EvaluationRun.status == "completed",
                                                              EvaluationRun.target_type != "redteam")
                                  .order_by(EvaluationRun.finished_at.desc()).limit(5))).scalars().all()
    last_rt = (await db.execute(select(EvaluationRun).where(EvaluationRun.organization_id == org, EvaluationRun.status == "completed",
                                                            EvaluationRun.target_type == "redteam")
                                .order_by(EvaluationRun.finished_at.desc()).limit(1))).scalar_one_or_none()
    sec = (await db.execute(select(AuditEvent).where(AuditEvent.organization_id == org,
                                                     AuditEvent.severity.in_(["warning", "critical"]))
                            .order_by(AuditEvent.chain_index.desc()).limit(8))).scalars().all()
    denied = (await db.execute(select(func.count()).select_from(ToolExecution).where(ToolExecution.organization_id == org,
                                                                                     ToolExecution.status == "denied",
                                                                                     ToolExecution.created_at >= since))).scalar_one()
    http_all = [s for k in metrics.window.keys() if k.startswith("http:") for s in metrics.window.samples(k, 900, synthetic=False)]
    http = metrics.RollingWindow.summarize(http_all, 900)
    recent = (await db.execute(select(AgentExecution, Agent.name).join(Agent, Agent.id == AgentExecution.agent_id)
                               .where(AgentExecution.organization_id == org).order_by(AgentExecution.created_at.desc()).limit(8))).all()
    bucket = (func.date_trunc("hour", AgentExecution.created_at) if db.bind.dialect.name == "postgresql"
              else func.strftime("%Y-%m-%d %H:00", AgentExecution.created_at)).label("bucket")
    series = (await db.execute(select(bucket, func.count(), func.avg(AgentExecution.latency_ms))
                               .where(AgentExecution.organization_id == org, AgentExecution.created_at >= since)
                               .group_by(bucket).order_by(bucket))).all()
    return {
        "window_hours": hours,
        "data_source": "persisted executions + in-process measured telemetry (no synthetic values)",
        "agents": {"active": agents.get("active", 0), "total": sum(v for k, v in agents.items() if k != "archived"), "by_status": agents},
        "mcp": {"servers": len(servers), "connected": sum(1 for s in servers if mcp_manager.is_connected(s.id)),
                "enabled_tools": tools_enabled, "policy_denials": denied},
        "executions": {"total": ex_total, "failed": ex_failed, "error_rate": (ex_failed / ex_total) if ex_total else None,
                       "avg_latency_ms": (sum((a or 0) * n for _, n, a in ex_rows) / ex_total) if ex_total else None,
                       "by_status": {s: n for s, n, _ in ex_rows},
                       "series": [{"t": str(t), "count": n, "avg_latency_ms": a} for t, n, a in series]},
        "workflows": {"total": sum(wf.values()), "by_status": wf},
        "model_usage": [{"provider": pr, "model": m, "calls": n, "tokens_in": int(ti or 0), "tokens_out": int(to or 0),
                         "cost_usd": round(float(c or 0), 6), "avg_latency_ms": a, "errors": int(e or 0), "is_mock": bool(mk)}
                        for pr, m, n, ti, to, c, a, e, mk in usage],
        "estimated_cost_usd": round(sum(float(r[5] or 0) for r in usage), 6),
        "evaluations": [{"id": r.id, "name": r.name, "pass_rate": (r.summary or {}).get("pass_rate"),
                         "groundedness": ((r.summary or {}).get("metrics") or {}).get("groundedness", {}).get("mean"),
                         "finished_at": r.finished_at.isoformat() if r.finished_at else None} for r in last_eval],
        "red_team": {"defense_rate": (last_rt.summary or {}).get("defense_rate"), "run_id": last_rt.id} if last_rt else None,
        "http": http,
        "security_events": [{"action": e.action, "outcome": e.outcome, "severity": e.severity, "actor": e.actor_email,
                             "at": e.created_at.isoformat(), "details": e.details} for e in sec],
        "recent_executions": [{"id": e.id, "agent": n, "status": e.status, "latency_ms": e.latency_ms, "is_mock": e.is_mock,
                               "created_at": e.created_at.isoformat()} for e, n in recent],
    }


@router.get("/metrics", summary="Measured performance metrics (P50/P95/P99, RPS, errors) by route and operation")
async def perf(window_s: int = Query(900, ge=60, le=3600), include_synthetic: bool = True,
               p: Principal = Depends(require("metrics:read"))) -> dict:
    keys = metrics.window.keys()
    syn = None if include_synthetic else False

    def group(prefix: str) -> list[dict]:
        out = []
        for k in keys:
            if k.startswith(prefix):
                ss = metrics.window.samples(k, window_s, synthetic=syn)
                if ss:
                    out.append({"name": k[len(prefix):], **metrics.RollingWindow.summarize(ss, window_s),
                                "synthetic_count": sum(s.synthetic for s in ss), "retries": metrics.window.retries.get(k, 0)})
        return sorted(out, key=lambda r: -r["count"])

    http_s = [s for k in keys if k.startswith("http:") for s in metrics.window.samples(k, window_s, synthetic=syn)]
    ex = runtime.executor()
    return {
        "window_s": window_s,
        "source": "measured in-process from real requests; synthetic = load-test traffic tagged by the runner",
        "http_total": {**metrics.RollingWindow.summarize(http_s, window_s), "synthetic_count": sum(s.synthetic for s in http_s)},
        "routes": group("http:"), "llm": group("llm:"), "tools": group("tool:"), "agents": group("agent:"),
        "workflows": group("workflow:"), "retrieval": group("retrieval:"), "embeddings": group("embedding:"),
        "timeseries": metrics.window.timeseries("http:", window_s, bucket_s=max(5, window_s // 60)),
        "concurrency": {**ex.snapshot(), "background_jobs": jobs.pending(), "ws_subscribers": bus.subscriber_count,
                        "ws_events_dropped": bus.dropped},
        "circuit_breakers": breakers.all(),
        "retries_total": sum(metrics.window.retries.values()),
        "system": metrics.system_snapshot(),
    }


class LoadTestIn(BaseModel):
    target: Literal["health", "list_agents", "knowledge_search", "dashboard", "agent_execute"] = "list_agents"
    requests: int = Field(200, ge=1, le=5000)
    concurrency: int = Field(20, ge=1, le=100)
    transport: Literal["network", "asgi"] = "network"
    agent_id: str | None = None


@router.post("/performance/loadtest", summary="Run a bounded synthetic load test against this API (real requests)")
async def loadtest(body: LoadTestIn, request: Request, p: Principal = Depends(require("loadtest:run"))) -> dict:
    if not get_settings().flag("load_test_runner"):
        raise FeatureDisabledError("Load test runner is disabled")
    prefix = get_settings().api_prefix
    auth = request.headers.get("authorization") or ""
    headers = {"authorization": auth, "x-aiecc-synthetic": synthetic_token()}
    if request.headers.get("x-api-key"):
        headers["x-api-key"] = request.headers["x-api-key"]
    spec = {
        "health": ("GET", "/health", None),
        "list_agents": ("GET", f"{prefix}/agents?limit=20", None),
        "dashboard": ("GET", f"{prefix}/dashboard/overview", None),
        "knowledge_search": ("POST", f"{prefix}/knowledge/search", {"query": "incident response latency", "strategy": "hybrid"}),
        "agent_execute": ("POST", f"{prefix}/agents/{body.agent_id}/execute", {"input": "What is the refund policy?"}),
    }[body.target]
    if body.target == "agent_execute" and not body.agent_id:
        raise FeatureDisabledError("agent_id required for agent_execute")
    if body.transport == "asgi":
        transport = httpx.ASGITransport(app=request.app)
        base = "http://loadtest"
    else:
        transport = None
        base = str(request.base_url).rstrip("/")
    sem = asyncio.Semaphore(body.concurrency)
    lat: list[float] = []
    codes: dict[int, int] = {}
    errors = 0
    limits = httpx.Limits(max_connections=body.concurrency, max_keepalive_connections=body.concurrency)
    async with httpx.AsyncClient(base_url=base, transport=transport, timeout=60, limits=limits, headers=headers) as client:
        async def one() -> None:
            nonlocal errors
            async with sem:
                t0 = time.perf_counter()
                try:
                    r = await client.request(spec[0], spec[1], json=spec[2])
                    codes[r.status_code] = codes.get(r.status_code, 0) + 1
                    if r.status_code >= 400:
                        errors += 1
                except httpx.HTTPError:
                    errors += 1
                    codes[0] = codes.get(0, 0) + 1
                lat.append((time.perf_counter() - t0) * 1000)

        t_start = time.perf_counter()
        await asyncio.gather(*(one() for _ in range(body.requests)))
        wall = time.perf_counter() - t_start
    lat.sort()
    buckets = [5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10_000]
    hist = [{"le_ms": b, "count": sum(1 for x in lat if x <= b)} for b in buckets]
    return {
        "target": body.target, "transport": body.transport, "requests": body.requests, "concurrency": body.concurrency,
        "wall_time_s": round(wall, 3), "throughput_rps": round(body.requests / wall, 2) if wall else None,
        "p50_ms": metrics.percentile(lat, 50), "p95_ms": metrics.percentile(lat, 95), "p99_ms": metrics.percentile(lat, 99),
        "max_ms": lat[-1] if lat else None, "errors": errors, "error_rate": errors / body.requests, "status_codes": codes,
        "histogram": hist, "label": "SYNTHETIC LOAD - real HTTP requests generated by the AIECC load runner",
    }


@router.get("/usage", summary="Token / cost / latency usage aggregated from persisted usage metrics")
async def usage(hours: int = Query(24, ge=1, le=720), db: AsyncSession = Depends(get_db),
                p: Principal = Depends(require("metrics:read"))) -> list[dict]:
    since = utcnow() - timedelta(hours=hours)
    rows = (await db.execute(select(UsageMetric.kind, UsageMetric.name, UsageMetric.provider, UsageMetric.model, func.count(),
                                    func.sum(UsageMetric.tokens_in + UsageMetric.tokens_out), func.sum(UsageMetric.cost_usd),
                                    func.avg(UsageMetric.latency_ms))
                             .where(UsageMetric.organization_id == p.org_id, UsageMetric.created_at >= since)
                             .group_by(UsageMetric.kind, UsageMetric.name, UsageMetric.provider, UsageMetric.model))).all()
    return [{"kind": k, "name": n, "provider": pr, "model": m, "count": c, "tokens": int(t or 0), "cost_usd": float(co or 0),
             "avg_latency_ms": a} for k, n, pr, m, c, t, co, a in rows]


class FlagsIn(BaseModel):
    flags: dict[str, bool]


@router.get("/settings")
async def settings(p: Principal = Depends(require("agents:read"))) -> dict:
    s = get_settings()
    return {"env": s.env, "feature_flags": s.feature_flags, "limits": {
        "max_agent_steps_hard_limit": s.max_agent_steps_hard_limit, "max_concurrent_executions": s.max_concurrent_executions,
        "max_queued_executions": s.max_queued_executions, "tool_timeout_s": s.tool_timeout_s,
        "agent_total_timeout_s": s.agent_total_timeout_s, "rate_limit_per_minute": s.rate_limit_per_minute,
        "upload_max_bytes": s.upload_max_bytes, "workflow_max_nodes": s.workflow_max_nodes},
        "http_tool_allowed_hosts": s.http_tool_allowed_hosts, "jobs_mode": s.jobs_mode, "otel_enabled": s.otel_enabled,
        "oidc": {"configured": bool(s.oidc_issuer_url), "issuer": s.oidc_issuer_url},
        "embedding": {"provider": s.embedding_provider, "model": s.embedding_model, "dim": s.embedding_dim}}


@router.put("/settings/feature-flags", summary="Toggle feature flags at runtime (process-local; persist via env)")
async def set_flags(body: FlagsIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("settings:manage"))) -> dict:
    s = get_settings()
    unknown = set(body.flags) - set(s.feature_flags)
    if unknown:
        raise FeatureDisabledError(f"Unknown flags: {sorted(unknown)}")
    before = dict(s.feature_flags)
    s.feature_flags.update(body.flags)
    await audit.record(db, org_id=p.org_id, action="settings.feature_flags", category="deployment", actor_id=p.user_id,
                       actor_email=p.email, severity="warning", details={"before": before, "after": s.feature_flags})
    return {"feature_flags": s.feature_flags}


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    """Auth handshake: first message must be {"type":"auth","token":"<access token>"} (tokens never in URLs)."""
    await ws.accept()
    try:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
        claims = decode_access_token(str(msg.get("token", ""))) if msg.get("type") == "auth" else None
    except Exception:  # noqa: BLE001
        claims = None
    if not claims:
        await ws.close(code=4401)
        return
    async with get_sessionmaker()() as db:
        user = (await db.execute(select(User).where(User.id == claims["sub"]))).scalar_one_or_none()
    if user is None or not user.is_active:
        await ws.close(code=4401)
        return
    org = user.organization_id
    q = bus.subscribe(org)
    await ws.send_json({"topic": "ws.ready", "data": {"org": org}})
    try:
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=20)
                await ws.send_text(encode(ev))
            except TimeoutError:
                await ws.send_json({"topic": "ws.ping", "ts": time.time()})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(org, q)
        with contextlib.suppress(Exception):
            await ws.close()


health_router = APIRouter(tags=["health"])


@health_router.get("/health", summary="Liveness")
async def health() -> dict:
    return {"status": "ok", "service": get_settings().app_name}


@health_router.get("/ready", summary="Readiness: database, redis, migrations, MCP")
async def ready() -> dict:
    out: dict = {"status": "ok"}
    t0 = time.perf_counter()
    try:
        async with get_sessionmaker()() as db:
            from sqlalchemy import text

            await db.execute(text("SELECT 1"))
            out["database"] = {"status": "ok", "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
                               "dialect": db.bind.dialect.name}
            if db.bind.dialect.name == "postgresql":
                rev = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
                ext = (await db.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))).scalar_one_or_none()
                out["database"].update(migration=rev, pgvector=ext)
    except Exception as exc:  # noqa: BLE001
        out["database"] = {"status": "error", "detail": str(exc)[:200]}
        out["status"] = "degraded"
    out["redis"] = await redis_ping()
    out["mcp"] = mcp_manager.snapshot()
    out["executor"] = runtime.executor().snapshot()
    return out
