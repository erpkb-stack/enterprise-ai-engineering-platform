"""Policy engine: every tool invocation (direct, agent, workflow, red-team) is authorized here.

Decision order (deny wins, first failing rule is reported):
  1. tool globally allowlisted (mcp_tools.is_enabled; tools are deny-by-default on discovery)
  2. server enabled
  3. server role restriction (mcp_servers.allowed_roles)
  4. caller permission (tools:execute for direct calls, agents:execute for agent calls, workflows:execute)
  5. agent-scoped allowlist (agent config `tools`)
  6. high-risk / requires-approval tools cannot run autonomously
  7. arguments validate against the tool's JSON schema
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import jsonschema

from app.models import McpServer, McpTool
from app.security.principal import Principal

CallerContext = Literal["direct", "agent", "workflow", "redteam"]


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    rule: str
    checks: list[dict[str, Any]] = field(default_factory=list)


def evaluate_tool_call(
    *,
    principal: Principal | None,
    server: McpServer,
    tool: McpTool,
    arguments: dict[str, Any],
    context: CallerContext,
    agent_tools: list[str] | None = None,
    approved: bool = False,
) -> PolicyDecision:
    checks: list[dict[str, Any]] = []

    def check(rule: str, ok: bool, reason: str) -> PolicyDecision | None:
        checks.append({"rule": rule, "passed": ok, "reason": reason})
        return None if ok else PolicyDecision(False, reason, rule, checks)

    qualified = f"{server.name}/{tool.name}"
    if d := check("tool_allowlisted", tool.is_enabled, f"Tool '{qualified}' is not allowlisted"):
        return d
    if d := check("server_enabled", server.is_enabled, f"Server '{server.name}' is disabled"):
        return d
    if principal is not None and server.allowed_roles:
        ok = bool(set(server.allowed_roles) & set(principal.roles))
        if d := check("server_role_restriction", ok, f"Role not permitted for server '{server.name}'"):
            return d
    if principal is not None:
        needed = {"direct": "tools:execute", "agent": "agents:execute", "workflow": "workflows:execute",
                  "redteam": "redteam:run"}[context]
        if d := check("caller_permission", principal.has(needed), f"Missing permission '{needed}'"):
            return d
    if context == "agent" or (context == "redteam" and agent_tools is not None):
        allowed = set(agent_tools or [])
        ok = qualified in allowed or tool.name in allowed
        if d := check("agent_tool_scope", ok, f"Tool '{qualified}' is not in this agent's tool allowlist"):
            return d
    if tool.requires_approval or tool.risk_level == "high":
        autonomous = context in ("agent", "redteam")
        ok = approved and not autonomous
        if principal is not None and context == "direct" and principal.has("tools:manage"):
            ok = True
        if d := check("approval_required", ok, f"Tool '{qualified}' is high-risk and requires human approval"):
            return d
    try:
        jsonschema.validate(arguments, tool.input_schema or {"type": "object"})
        schema_ok, why = True, "arguments valid"
    except jsonschema.ValidationError as exc:
        schema_ok, why = False, f"Invalid arguments: {exc.message}"
    if d := check("argument_schema", schema_ok, why):
        return d
    return PolicyDecision(True, "allowed", "all", checks)
