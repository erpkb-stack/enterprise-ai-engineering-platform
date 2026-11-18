from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import Pagination, get_owned, paginate, to_dict
from app.core.db import get_db
from app.core.errors import ConflictError, UpstreamError
from app.core.ids import new_id, utcnow
from app.core.resilience import breakers
from app.integrations.mcp.adapters import ALLOWED_STDIO_COMMANDS
from app.integrations.mcp.manager import mcp_manager
from app.models import McpServer, McpTool, ToolExecution
from app.security import audit
from app.security.crypto import encrypt_secret
from app.security.principal import Principal, require
from app.services.tool_service import invoke_tool

router = APIRouter(prefix="/mcp", tags=["mcp"])
tools_router = APIRouter(prefix="/tools", tags=["tools"])

SERVER_HIDDEN = {"credential_encrypted"}


class ServerIn(BaseModel):
    name: str = Field(min_length=2, max_length=200, pattern=r"^[a-zA-Z0-9_\-. ]+$")
    description: str = ""
    transport: Literal["stdio", "streamable_http"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = Field(None, pattern=r"^https?://")
    auth_type: Literal["none", "api_key", "oauth2", "service"] = "none"
    credentials: dict[str, Any] | None = Field(None, description="Write-only; encrypted at rest, never returned")
    timeout_s: float = Field(20, ge=1, le=120)
    max_retries: int = Field(2, ge=0, le=5)
    allowed_roles: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> ServerIn:
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio transport requires command")
        if self.transport == "streamable_http" and not self.url:
            raise ValueError("streamable_http transport requires url")
        return self


class ServerPatch(BaseModel):
    description: str | None = None
    args: list[str] | None = None
    url: str | None = None
    auth_type: Literal["none", "api_key", "oauth2", "service"] | None = None
    credentials: dict[str, Any] | None = None
    timeout_s: float | None = Field(None, ge=1, le=120)
    allowed_roles: list[str] | None = None
    is_enabled: bool | None = None


class ToolPatch(BaseModel):
    is_enabled: bool | None = None
    risk_level: Literal["low", "medium", "high"] | None = None
    requires_approval: bool | None = None


class ToolExec(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


def _server(s: McpServer) -> dict:
    d = to_dict(s, exclude=SERVER_HIDDEN | {"config_history"})
    d["has_credentials"] = bool(s.credential_encrypted)
    d["live_connected"] = mcp_manager.is_connected(s.id)
    d["circuit"] = breakers.get(f"mcp:{s.id}").snapshot()
    return d


def _snapshot(s: McpServer) -> dict:
    return {"version": s.config_version, "transport": s.transport, "command": s.command, "args": s.args, "url": s.url,
            "auth_type": s.auth_type, "timeout_s": s.timeout_s, "allowed_roles": s.allowed_roles, "at": utcnow().isoformat()}


@router.post("/servers", status_code=201)
async def register(body: ServerIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("mcp:manage"))) -> dict:
    if body.transport == "stdio" and body.command.split("/")[-1] not in ALLOWED_STDIO_COMMANDS and not body.command.split("/")[-1].startswith("python"):
        raise ConflictError(f"STDIO command must be one of {sorted(ALLOWED_STDIO_COMMANDS)}")
    if (await db.execute(select(McpServer).where(McpServer.organization_id == p.org_id, McpServer.name == body.name))).scalar_one_or_none():
        raise ConflictError("Server name already exists")
    s = McpServer(id=new_id(), organization_id=p.org_id, **body.model_dump(exclude={"credentials"}),
                  credential_encrypted=encrypt_secret(json.dumps(body.credentials)) if body.credentials else None)
    s.config_history = [_snapshot(s)]
    db.add(s)
    await db.flush()
    await audit.record(db, org_id=p.org_id, action="mcp.server_register", category="mcp_connection", actor_id=p.user_id,
                       actor_email=p.email, resource_type="mcp_server", resource_id=s.id,
                       details={"name": s.name, "transport": s.transport, "auth_type": s.auth_type})
    return _server(s)


@router.get("/servers")
async def list_servers(page: Pagination = Depends(), db: AsyncSession = Depends(get_db),
                       p: Principal = Depends(require("mcp:read"))) -> dict:
    stmt = select(McpServer).where(McpServer.organization_id == p.org_id).order_by(McpServer.name)
    res = await paginate(db, stmt, page, ser=_server)
    counts = dict((await db.execute(select(McpTool.server_id, func.count()).where(McpTool.organization_id == p.org_id)
                                    .group_by(McpTool.server_id))).all())
    for item in res["items"]:
        item["tool_count"] = counts.get(item["id"], 0)
    return res


@router.get("/servers/{server_id}")
async def get_server(server_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("mcp:read"))) -> dict:
    s = await get_owned(db, McpServer, server_id, p.org_id)
    return {**_server(s), "config_history": s.config_history}


@router.put("/servers/{server_id}", summary="Update server config (versioned); reconnect to apply")
async def update_server(server_id: str, body: ServerPatch, db: AsyncSession = Depends(get_db),
                        p: Principal = Depends(require("mcp:manage"))) -> dict:
    s = await get_owned(db, McpServer, server_id, p.org_id)
    data = body.model_dump(exclude_unset=True, exclude={"credentials"})
    for k, v in data.items():
        setattr(s, k, v)
    if body.credentials is not None:
        s.credential_encrypted = encrypt_secret(json.dumps(body.credentials)) if body.credentials else None
    s.config_version += 1
    s.config_history = [*s.config_history, _snapshot(s)][-20:]
    await audit.record(db, org_id=p.org_id, action="mcp.server_update", category="mcp_connection", actor_id=p.user_id,
                       actor_email=p.email, resource_type="mcp_server", resource_id=s.id,
                       details={"fields": sorted(data) + (["credentials"] if body.credentials is not None else []),
                                "version": s.config_version})
    if mcp_manager.is_connected(s.id):
        await mcp_manager.disconnect(s.id)
        s.status = "disconnected"
    return _server(s)


@router.delete("/servers/{server_id}")
async def delete_server(server_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("mcp:manage"))) -> dict:
    s = await get_owned(db, McpServer, server_id, p.org_id)
    await mcp_manager.disconnect(s.id)
    await audit.record(db, org_id=p.org_id, action="mcp.server_delete", category="mcp_connection", actor_id=p.user_id,
                       actor_email=p.email, resource_type="mcp_server", resource_id=s.id, details={"name": s.name})
    await db.delete(s)
    return {"ok": True}


async def _discover(db: AsyncSession, s: McpServer, org: str) -> dict:
    adapter = await mcp_manager.ensure(s)
    specs = await adapter.discover_tools()
    resources = await adapter.discover_resources()
    existing = {t.name: t for t in (await db.execute(select(McpTool).where(McpTool.server_id == s.id))).scalars()}
    now = utcnow()
    new = []
    for sp in specs:
        t = existing.get(sp.name)
        if t is None:
            risky = any(w in sp.name.lower() for w in ("delete", "drop", "remove", "write", "exec", "transfer", "send"))
            t = McpTool(organization_id=org, server_id=s.id, name=sp.name, description=sp.description,
                        input_schema=sp.input_schema, is_enabled=False, risk_level="high" if risky else "medium",
                        requires_approval=risky, discovered_at=now)
            db.add(t)
            new.append(sp.name)
        else:
            t.description, t.input_schema, t.discovered_at = sp.description, sp.input_schema, now
    s.status = "connected"
    s.last_health = await adapter.health_check()
    await db.flush()
    return {"tools": [sp.model_dump() for sp in specs], "resources": resources, "new_tools": new,
            "note": "Newly discovered tools are disabled until allowlisted in the Tool Registry."}


@router.post("/servers/{server_id}/connect", summary="Connect, authenticate and discover tools")
async def connect(server_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("mcp:connect"))) -> dict:
    s = await get_owned(db, McpServer, server_id, p.org_id)
    if not s.is_enabled:
        raise ConflictError("Server is disabled")
    try:
        result = await _discover(db, s, p.org_id)
    except Exception as exc:  # noqa: BLE001
        s.status, s.last_health = "error", {"status": "error", "detail": str(exc)[:300], "at": utcnow().isoformat()}
        await audit.record(db, org_id=p.org_id, action="mcp.connect", category="mcp_connection", actor_id=p.user_id,
                           actor_email=p.email, resource_type="mcp_server", resource_id=s.id, outcome="failure",
                           details={"error": str(exc)[:300]})
        await db.commit()
        raise UpstreamError(f"Could not connect to MCP server: {exc}") from exc
    await audit.record(db, org_id=p.org_id, action="mcp.connect", category="mcp_connection", actor_id=p.user_id,
                       actor_email=p.email, resource_type="mcp_server", resource_id=s.id,
                       details={"tools": len(result["tools"]), "new_tools": result["new_tools"]})
    return {**result, "server": _server(s)}


@router.post("/servers/{server_id}/disconnect")
async def disconnect(server_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("mcp:connect"))) -> dict:
    s = await get_owned(db, McpServer, server_id, p.org_id)
    await mcp_manager.disconnect(s.id)
    s.status = "disconnected"
    await audit.record(db, org_id=p.org_id, action="mcp.disconnect", category="mcp_connection", actor_id=p.user_id,
                       actor_email=p.email, resource_type="mcp_server", resource_id=s.id)
    return _server(s)


@router.post("/servers/{server_id}/health")
async def health(server_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("mcp:read"))) -> dict:
    s = await get_owned(db, McpServer, server_id, p.org_id)
    a = mcp_manager.get(s.id)
    h = await a.health_check() if a else {"status": "disconnected"}
    s.last_health = {**h, "at": utcnow().isoformat()}
    if h.get("status") != "ok" and s.status == "connected":
        s.status = "unhealthy"
    return s.last_health


@router.get("/servers/{server_id}/tools")
async def server_tools(server_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("mcp:read"))) -> list[dict]:
    s = await get_owned(db, McpServer, server_id, p.org_id)
    return [to_dict(t) for t in (await db.execute(select(McpTool).where(McpTool.server_id == s.id).order_by(McpTool.name))).scalars()]


# ---------------------------------------------------------------- tool registry ------------------
def _tool(t: McpTool, server: McpServer) -> dict:
    d = to_dict(t)
    d["server_name"] = server.name
    d["qualified_name"] = f"{server.name}/{t.name}"
    d["avg_latency_ms"] = round(t.total_latency_ms / t.call_count, 1) if t.call_count else None
    d["error_rate"] = round(t.error_count / t.call_count, 4) if t.call_count else None
    return d


@tools_router.get("")
async def registry(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("tools:read"))) -> list[dict]:
    rows = (await db.execute(select(McpTool, McpServer).join(McpServer, McpServer.id == McpTool.server_id)
                             .where(McpTool.organization_id == p.org_id).order_by(McpServer.name, McpTool.name))).all()
    return [_tool(t, s) for t, s in rows]


@tools_router.put("/{tool_id}", summary="Allowlist / risk / approval settings")
async def update_tool(tool_id: str, body: ToolPatch, db: AsyncSession = Depends(get_db),
                      p: Principal = Depends(require("tools:manage"))) -> dict:
    t = await get_owned(db, McpTool, tool_id, p.org_id)
    before = {"is_enabled": t.is_enabled, "risk_level": t.risk_level, "requires_approval": t.requires_approval}
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    s = (await db.execute(select(McpServer).where(McpServer.id == t.server_id))).scalar_one()
    await audit.record(db, org_id=p.org_id, action="tool.policy_change", category="permission_change", actor_id=p.user_id,
                       actor_email=p.email, resource_type="mcp_tool", resource_id=t.id, severity="warning",
                       details={"tool": f"{s.name}/{t.name}", "before": before, "after": body.model_dump(exclude_unset=True)})
    return _tool(t, s)


@tools_router.post("/{tool_id}/execute", summary="Direct tool invocation (policy-checked, audited)")
async def execute_tool(tool_id: str, body: ToolExec, db: AsyncSession = Depends(get_db),
                       p: Principal = Depends(require("tools:execute"))) -> dict:
    t = await get_owned(db, McpTool, tool_id, p.org_id)
    s = (await db.execute(select(McpServer).where(McpServer.id == t.server_id))).scalar_one()
    res, decision = await invoke_tool(db, org_id=p.org_id, principal=p, qualified=f"{s.name}/{t.name}",
                                      arguments=body.arguments, context="direct")
    return {"result": res.model_dump(), "policy": {"allowed": decision.allowed, "rule": decision.rule,
                                                   "reason": decision.reason, "checks": decision.checks}}


@tools_router.get("/executions")
async def tool_executions(status: str | None = None, page: Pagination = Depends(), db: AsyncSession = Depends(get_db),
                          p: Principal = Depends(require("tools:read"))) -> dict:
    stmt = select(ToolExecution).where(ToolExecution.organization_id == p.org_id)
    if status:
        stmt = stmt.where(ToolExecution.status == status)
    return await paginate(db, stmt.order_by(ToolExecution.created_at.desc()), page)
