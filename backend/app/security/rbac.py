"""RBAC catalog: permissions and the seven system roles.

Permissions are coarse-grained `resource:action` codes. Finer-grained controls (tool allowlists,
per-server role restrictions, agent tool scopes) are enforced by the PolicyEngine.
"""

from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    "agents:read": "View agents and executions",
    "agents:write": "Create and edit agents",
    "agents:execute": "Execute agents",
    "agents:lifecycle": "Activate, deactivate and archive agents",
    "providers:read": "View model providers",
    "providers:manage": "Configure model providers and credentials",
    "mcp:read": "View MCP servers",
    "mcp:manage": "Register and configure MCP servers",
    "mcp:connect": "Connect/disconnect MCP servers and discover tools",
    "tools:read": "View tool registry",
    "tools:manage": "Change tool allowlists and risk settings",
    "tools:execute": "Invoke tools directly",
    "prompts:read": "View prompts",
    "prompts:write": "Create and edit prompts",
    "prompts:test": "Run prompt tests",
    "prompts:approve": "Approve or reject prompt versions",
    "knowledge:read": "Search knowledge base",
    "knowledge:write": "Upload and delete documents",
    "evaluations:read": "View evaluations",
    "evaluations:run": "Run evaluations and manage datasets",
    "redteam:run": "Run red-team suites (sandboxed)",
    "experiments:read": "View experiments",
    "experiments:write": "Create and run experiments",
    "workflows:read": "View workflows",
    "workflows:write": "Create and edit workflows",
    "workflows:execute": "Execute workflows",
    "workflows:approve": "Approve human-approval workflow steps",
    "metrics:read": "View performance metrics",
    "loadtest:run": "Run synthetic load tests",
    "audit:read": "Read audit events",
    "audit:verify": "Verify audit chain integrity",
    "security:manage": "Manage users, roles and API keys",
    "architecture:read": "View architecture designs",
    "architecture:write": "Create architecture designs and ADRs",
    "codereview:run": "Run code reviews",
    "settings:manage": "Manage platform settings and feature flags",
}

_READ = [p for p in PERMISSIONS if p.endswith(":read") and not p.startswith("audit")]

ROLES: dict[str, dict] = {
    "Platform Administrator": {"description": "Full control of the platform", "permissions": list(PERMISSIONS)},
    "Engineering Administrator": {
        "description": "Manages engineering resources, providers and integrations",
        "permissions": [p for p in PERMISSIONS if p not in ("security:manage", "audit:verify", "settings:manage")],
    },
    "AI Engineer": {
        "description": "Builds and evaluates agents, prompts, RAG and workflows",
        "permissions": _READ
        + [
            "agents:write", "agents:execute", "agents:lifecycle", "mcp:connect", "tools:execute",
            "prompts:write", "prompts:test", "knowledge:write", "evaluations:run", "redteam:run",
            "experiments:write", "workflows:write", "workflows:execute", "architecture:write",
            "codereview:run", "loadtest:run",
        ],
    },
    "Developer": {
        "description": "Consumes agents, tools and workflows",
        "permissions": _READ
        + ["agents:execute", "tools:execute", "prompts:test", "workflows:execute", "codereview:run", "architecture:write"],
    },
    "Security Reviewer": {
        "description": "Reviews prompts, tools and red-team results",
        "permissions": _READ
        + ["prompts:approve", "redteam:run", "audit:read", "audit:verify", "tools:manage", "evaluations:run", "workflows:approve"],
    },
    "Auditor": {"description": "Read-only access including the audit trail", "permissions": _READ + ["audit:read", "audit:verify"]},
    "Read-Only User": {"description": "Read-only access to non-sensitive resources", "permissions": _READ},
}
