"""MCP connection manager: one pooled, long-lived adapter per registered server."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.integrations.mcp.adapters import AdapterError, McpHttpAdapter, McpStdioAdapter
from app.integrations.tools.base import ToolAdapter
from app.integrations.tools.builtin import BuiltinToolAdapter
from app.models import McpServer
from app.security.crypto import decrypt_secret

log = get_logger(__name__)
DEMO_SERVER_PATH = str(Path(__file__).with_name("demo_server.py"))
DEMO_TOKEN = "{AIECC_DEMO_SERVER}"  # noqa: S105 - placeholder token, not a credential


def _portable_args(args: list[str]) -> list[str]:
    """Resolve the bundled demo server path on whichever host/container is running the platform."""
    out = []
    for a in args:
        if a == DEMO_TOKEN or a.endswith("app/integrations/mcp/demo_server.py"):
            out.append(DEMO_SERVER_PATH)
        else:
            out.append(a)
    return out


def build_adapter(server: McpServer) -> ToolAdapter:
    creds: dict[str, Any] = {}
    raw = decrypt_secret(server.credential_encrypted)
    if raw:
        try:
            creds = json.loads(raw)
        except json.JSONDecodeError:
            creds = {"api_key": raw}
    if server.transport == "builtin":
        return BuiltinToolAdapter(server.organization_id)
    if server.transport == "stdio":
        if not server.command:
            raise AdapterError("STDIO server requires a command")
        return McpStdioAdapter(server.id, server.name, server.command, _portable_args(list(server.args or [])),
                               env=creds.get("env") if isinstance(creds.get("env"), dict) else None,
                               timeout_s=server.timeout_s)
    if server.transport == "streamable_http":
        if not server.url:
            raise AdapterError("Streamable HTTP server requires a url")
        return McpHttpAdapter(server.id, server.name, server.url, server.auth_type, creds, timeout_s=server.timeout_s)
    raise AdapterError(f"Unsupported transport '{server.transport}'")


class McpConnectionManager:
    def __init__(self) -> None:
        self._adapters: dict[str, ToolAdapter] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, sid: str) -> asyncio.Lock:
        return self._locks.setdefault(sid, asyncio.Lock())

    def get(self, server_id: str) -> ToolAdapter | None:
        return self._adapters.get(server_id)

    def is_connected(self, server_id: str) -> bool:
        a = self._adapters.get(server_id)
        if a is None:
            return False
        return getattr(a, "connected", True)

    async def connect(self, server: McpServer) -> ToolAdapter:
        async with self._lock(server.id):
            existing = self._adapters.get(server.id)
            if existing is not None and getattr(existing, "connected", True):
                return existing
            adapter = build_adapter(server)
            await adapter.initialize()
            await adapter.authenticate()
            self._adapters[server.id] = adapter
            log.info("mcp_connected server=%s transport=%s", server.name, server.transport)
            return adapter

    async def ensure(self, server: McpServer) -> ToolAdapter:
        a = self._adapters.get(server.id)
        if a is not None and getattr(a, "connected", True):
            return a
        return await self.connect(server)

    async def disconnect(self, server_id: str) -> None:
        async with self._lock(server_id):
            a = self._adapters.pop(server_id, None)
            if a is not None:
                await a.shutdown()

    async def shutdown_all(self) -> None:
        await asyncio.gather(*(a.shutdown() for a in self._adapters.values()), return_exceptions=True)
        self._adapters.clear()

    def snapshot(self) -> dict[str, Any]:
        return {"connected": len(self._adapters), "servers": list(self._adapters)}


mcp_manager = McpConnectionManager()
