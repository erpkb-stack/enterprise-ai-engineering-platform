"""Tool invocation use case: resolve -> authorize (policy) -> execute (timeout, breaker) -> record -> audit."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError, PolicyViolationError
from app.core.resilience import breakers
from app.integrations.mcp.manager import mcp_manager
from app.integrations.tools.base import ToolResult
from app.models import McpServer, McpTool, ToolExecution, UsageMetric
from app.observability import metrics
from app.observability.tracing import span
from app.security import audit
from app.security.audit import scrub
from app.security.policy import CallerContext, PolicyDecision, evaluate_tool_call
from app.security.principal import Principal


async def resolve_tool(db: AsyncSession, org_id: str, qualified: str) -> tuple[McpServer, McpTool]:
    """Accepts 'server/tool' or a bare tool name (must be unambiguous within the org)."""
    q = select(McpServer, McpTool).join(McpTool, McpTool.server_id == McpServer.id).where(McpServer.organization_id == org_id)
    if "/" in qualified:
        sname, tname = qualified.split("/", 1)
        q = q.where(McpServer.name == sname, McpTool.name == tname)
    else:
        q = q.where(McpTool.name == qualified)
    rows = (await db.execute(q)).all()
    if not rows:
        raise NotFoundError(f"Tool '{qualified}' not found")
    if len(rows) > 1:
        raise PolicyViolationError(f"Tool name '{qualified}' is ambiguous; use 'server/tool'")
    return rows[0][0], rows[0][1]


async def list_agent_tools(db: AsyncSession, org_id: str, names: list[str]) -> list[tuple[McpServer, McpTool]]:
    out = []
    for n in names:
        try:
            out.append(await resolve_tool(db, org_id, n))
        except (NotFoundError, PolicyViolationError):
            continue
    return out


async def invoke_tool(
    db: AsyncSession,
    *,
    org_id: str,
    principal: Principal | None,
    qualified: str,
    arguments: dict[str, Any],
    context: CallerContext,
    agent_tools: list[str] | None = None,
    agent_execution_id: str | None = None,
    workflow_execution_id: str | None = None,
    approved: bool = False,
    timeout_s: float | None = None,
) -> tuple[ToolResult, PolicyDecision]:
    server, tool = await resolve_tool(db, org_id, qualified)
    decision = evaluate_tool_call(principal=principal, server=server, tool=tool, arguments=arguments,
                                  context=context, agent_tools=agent_tools, approved=approved)
    name = f"{server.name}/{tool.name}"
    base = dict(organization_id=org_id, tool_id=tool.id, server_id=server.id, agent_execution_id=agent_execution_id,
                workflow_execution_id=workflow_execution_id, user_id=principal.user_id if principal else None,
                tool_name=name, arguments=scrub(arguments))
    if not decision.allowed:
        db.add(ToolExecution(**base, status="denied", error=decision.reason, latency_ms=0))
        await audit.record(db, org_id=org_id, action="tool.denied", category="tool_execution",
                           actor_id=principal.user_id if principal else None,
                           actor_email=principal.email if principal else None, resource_type="mcp_tool",
                           resource_id=tool.id, outcome="denied", severity="warning",
                           details={"tool": name, "rule": decision.rule, "reason": decision.reason, "context": context})
        metrics.record_op("tool", name, 0, False)
        return ToolResult(ok=False, content="", error=f"POLICY DENIED: {decision.reason}"), decision

    timeout = min(timeout_s or server.timeout_s, get_settings().tool_timeout_s)
    breaker = breakers.get(f"mcp:{server.id}", failure_threshold=5, reset_timeout_s=20)
    t0 = time.perf_counter()
    with span("tool.execute", tool=name, transport=server.transport, context=context):
        try:
            adapter = await mcp_manager.ensure(server)
            server.status = "connected"
            result = await breaker.call(lambda: adapter.execute(tool.name, arguments, timeout))
        except Exception as exc:  # noqa: BLE001
            result = ToolResult(ok=False, content="", error=str(exc)[:500])
    latency = (time.perf_counter() - t0) * 1000
    result.latency_ms = latency
    tool.call_count += 1
    tool.total_latency_ms += latency
    if not result.ok:
        tool.error_count += 1
    db.add(ToolExecution(**base, status="success" if result.ok else "error", error=result.error,
                         result_preview=(result.content or "")[:2000], latency_ms=latency))
    db.add(UsageMetric(organization_id=org_id, kind="tool", name=name, latency_ms=latency, success=result.ok))
    metrics.record_op("tool", name, latency, result.ok)
    await audit.record(db, org_id=org_id, action="tool.execute", category="tool_execution",
                       actor_id=principal.user_id if principal else None,
                       actor_email=principal.email if principal else None, resource_type="mcp_tool", resource_id=tool.id,
                       outcome="success" if result.ok else "failure",
                       severity="warning" if result.injection_flags else "info",
                       details={"tool": name, "context": context, "latency_ms": round(latency, 1),
                                "injection_flags": len(result.injection_flags)})
    return result, decision
