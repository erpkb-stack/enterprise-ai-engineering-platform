"""In-process built-in tools exposed through the same ToolAdapter contract as MCP servers."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.core.safe_eval import UnsafeExpression, safe_eval
from app.integrations.tools.base import ToolAdapter, ToolResult, ToolSpec, sanitize_tool_output

# Injected at startup to avoid an import cycle with the RAG service:
#   async def knowledge_search(org_id, query, top_k, collection) -> list[dict]
KnowledgeSearchFn = Callable[[str, str, int, str | None], Awaitable[list[dict[str, Any]]]]

BUILTIN_TOOLS = [
    ToolSpec(
        name="knowledge_search",
        description="Search the organization's knowledge base (RAG) and return cited passages",
        input_schema={"type": "object", "properties": {"query": {"type": "string", "minLength": 2},
                                                        "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                                                        "collection": {"type": "string"}},
                      "required": ["query"], "additionalProperties": False},
        risk_level="low",
    ),
    ToolSpec(
        name="calculator",
        description="Evaluate an arithmetic expression safely (no code execution)",
        input_schema={"type": "object", "properties": {"expression": {"type": "string", "maxLength": 200}},
                      "required": ["expression"], "additionalProperties": False},
        risk_level="low",
    ),
    ToolSpec(
        name="current_time",
        description="Return the current UTC time",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        risk_level="low",
    ),
]


class BuiltinToolAdapter(ToolAdapter):
    transport = "builtin"
    knowledge_search: KnowledgeSearchFn | None = None

    def __init__(self, org_id: str) -> None:
        self.org_id = org_id
        self._ready = False

    async def initialize(self) -> None:
        self._ready = True

    async def authenticate(self) -> None:
        return None  # in-process; authorization is enforced by the policy engine

    async def discover_tools(self) -> list[ToolSpec]:
        return list(BUILTIN_TOOLS)

    async def execute(self, tool: str, arguments: dict[str, Any], timeout_s: float) -> ToolResult:
        t0 = time.perf_counter()
        try:
            if tool == "calculator":
                value = safe_eval(str(arguments["expression"]))
                content, structured = str(value), {"result": value}
            elif tool == "current_time":
                now = datetime.now(UTC).isoformat()
                content, structured = now, {"utc": now}
            elif tool == "knowledge_search":
                if BuiltinToolAdapter.knowledge_search is None:
                    raise RuntimeError("knowledge_search not wired")
                hits = await BuiltinToolAdapter.knowledge_search(
                    self.org_id, arguments["query"], int(arguments.get("top_k", 4)), arguments.get("collection")
                )
                content = "\n".join(
                    f"[{i}] ({h['filename']} #chunk{h['ordinal']}) {h['content']}" for i, h in enumerate(hits, 1)
                ) or "No matching passages."
                structured = {"passages": hits}
            else:
                return ToolResult(ok=False, content="", error=f"unknown builtin tool '{tool}'")
        except (UnsafeExpression, KeyError, ZeroDivisionError, ValueError, TypeError) as exc:
            return ToolResult(ok=False, content="", error=str(exc), latency_ms=(time.perf_counter() - t0) * 1000)
        text, truncated, flags = sanitize_tool_output(content)
        return ToolResult(ok=True, content=text, structured=json.loads(json.dumps(structured, default=str)),
                          latency_ms=(time.perf_counter() - t0) * 1000, truncated=truncated, injection_flags=flags)

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok" if self._ready else "disconnected", "transport": "builtin"}

    async def shutdown(self) -> None:
        self._ready = False
