"""Standard Tool Adapter contract. Every integration (built-in, MCP STDIO, MCP Streamable HTTP)
implements this interface so the policy engine and tool service treat them uniformly."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

MAX_TOOL_OUTPUT_CHARS = 20_000

_INJECTION_PATTERNS = [
    re.compile(p, re.I)
    for p in [
        r"ignore (all|any|the)? ?(previous|prior|above) (instructions|prompts?)",
        r"disregard (the|your) (system|previous) (prompt|instructions)",
        r"you are now (in )?(developer|dan|jailbreak) mode",
        r"reveal (your|the) (system prompt|secret|api key|credentials)",
        r"<\s*/?\s*(system|instructions)\s*>",
        r"call (the )?tool .{0,40}(delete|drop|exfiltrate|transfer)",
        r"send (all|the) (data|secrets|credentials) to",
    ]
]
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    risk_level: str = "low"


class ToolResult(BaseModel):
    ok: bool
    content: str
    structured: Any | None = None
    latency_ms: float = 0.0
    truncated: bool = False
    injection_flags: list[str] = Field(default_factory=list)
    error: str | None = None


def sanitize_tool_output(text: str) -> tuple[str, bool, list[str]]:
    """External tool output is untrusted: strip control chars, bound size, flag injection attempts."""
    text = _CTRL.sub("", text)
    truncated = len(text) > MAX_TOOL_OUTPUT_CHARS
    if truncated:
        text = text[:MAX_TOOL_OUTPUT_CHARS] + "\n…[truncated]"
    flags = [p.pattern for p in _INJECTION_PATTERNS if p.search(text)]
    return text, truncated, flags


class ToolAdapter(ABC):
    """Lifecycle: initialize -> authenticate -> discover_tools -> execute* -> health_check* -> shutdown."""

    transport: str = "abstract"

    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def authenticate(self) -> None: ...

    @abstractmethod
    async def discover_tools(self) -> list[ToolSpec]: ...

    @abstractmethod
    async def execute(self, tool: str, arguments: dict[str, Any], timeout_s: float) -> ToolResult: ...

    @abstractmethod
    async def health_check(self) -> dict[str, Any]: ...

    @abstractmethod
    async def shutdown(self) -> None: ...

    async def discover_resources(self) -> list[dict[str, Any]]:
        return []
