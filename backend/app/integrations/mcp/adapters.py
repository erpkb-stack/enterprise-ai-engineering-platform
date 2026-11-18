"""MCP client adapters (STDIO and Streamable HTTP) implementing the ToolAdapter contract.

Lifecycle design: the MCP SDK's transports are anyio context managers whose cancel scopes must be
exited by the same task that entered them. Each adapter therefore owns a dedicated long-lived
"session task" that opens the transport + ClientSession, signals readiness, and waits for a stop
event. Request handlers on other tasks call methods on the shared session (JSON-RPC multiplexes
concurrent requests), bounded by a per-server semaphore. This gives connection reuse (pooling)
and clean shutdown.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from typing import Any

import httpx
import httpx2
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from app.core.logging import get_logger
from app.integrations.tools.base import ToolAdapter, ToolResult, ToolSpec, sanitize_tool_output

log = get_logger(__name__)

# Only these executables may be launched for STDIO servers (prevents registering arbitrary binaries).
ALLOWED_STDIO_COMMANDS = {"python", "python3", "uv", "uvx", "npx", "node", "docker"}


class AdapterError(Exception):
    pass


def _resolve_command(cmd: str) -> str:
    base = os.path.basename(cmd)
    if cmd in ("python", "python3", sys.executable) or base.startswith("python"):
        return sys.executable
    if base not in ALLOWED_STDIO_COMMANDS:
        raise AdapterError(f"STDIO command '{cmd}' is not in the allowlist {sorted(ALLOWED_STDIO_COMMANDS)}")
    path = shutil.which(base)
    if not path:
        raise AdapterError(f"Command '{base}' not found on PATH")
    return path


class _SessionAdapter(ToolAdapter):
    def __init__(self, server_id: str, name: str, timeout_s: float = 20.0, max_concurrency: int = 8) -> None:
        self.server_id = server_id
        self.name = name
        self.timeout_s = timeout_s
        self._session: ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._error: BaseException | None = None
        self._sem = asyncio.Semaphore(max_concurrency)
        self.server_info: dict[str, Any] = {}

    async def _open(self, stack_enter) -> ClientSession:  # pragma: no cover - abstract
        raise NotImplementedError

    async def _runner(self) -> None:
        try:
            async with self._transport() as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write, read_timeout_seconds=self.timeout_s) as session:
                    init = await session.initialize()
                    self.server_info = {"name": init.server_info.name, "version": init.server_info.version,
                                        "protocol_version": getattr(init, "protocol_version", None)}
                    self._session = session
                    self._ready.set()
                    await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 - surface any startup failure to the caller
            self._error = exc
            self._ready.set()
        finally:
            self._session = None

    def _transport(self):  # pragma: no cover - abstract
        raise NotImplementedError

    async def initialize(self) -> None:
        if self._task and not self._task.done():
            return
        self._ready.clear()
        self._stop.clear()
        self._error = None
        self._task = asyncio.create_task(self._runner(), name=f"mcp-session-{self.name}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.timeout_s)
        except TimeoutError as exc:
            await self.shutdown()
            raise AdapterError(f"MCP server '{self.name}' did not initialize within {self.timeout_s}s") from exc
        if self._error is not None:
            err = self._error
            await self.shutdown()
            msg = str(err)
            if isinstance(err, BaseExceptionGroup):
                msg = "; ".join(str(e) for e in err.exceptions)
            raise AdapterError(f"MCP server '{self.name}' failed to start: {msg or type(err).__name__}")

    def _require(self) -> ClientSession:
        if self._session is None:
            raise AdapterError(f"MCP server '{self.name}' is not connected")
        return self._session

    async def discover_tools(self) -> list[ToolSpec]:
        s = self._require()
        res = await s.list_tools()
        return [ToolSpec(name=t.name, description=(t.description or "")[:2000], input_schema=t.input_schema or {})
                for t in res.tools]

    async def discover_resources(self) -> list[dict[str, Any]]:
        s = self._require()
        try:
            res = await s.list_resources()
        except Exception:  # noqa: BLE001 - resources capability is optional
            return []
        return [{"uri": str(r.uri), "name": r.name, "description": r.description, "mime_type": r.mime_type}
                for r in res.resources]

    async def execute(self, tool: str, arguments: dict[str, Any], timeout_s: float) -> ToolResult:
        s = self._require()
        t0 = time.perf_counter()
        async with self._sem:
            try:
                res = await asyncio.wait_for(s.call_tool(tool, arguments, read_timeout_seconds=timeout_s), timeout_s + 1)
            except TimeoutError:
                return ToolResult(ok=False, content="", error=f"tool timed out after {timeout_s}s",
                                  latency_ms=(time.perf_counter() - t0) * 1000)
        parts = []
        for c in res.content:
            parts.append(getattr(c, "text", None) or f"<{getattr(c, 'type', 'content')}>")
        text, truncated, flags = sanitize_tool_output("\n".join(parts))
        latency = (time.perf_counter() - t0) * 1000
        if res.is_error:
            return ToolResult(ok=False, content=text, error=text[:500] or "tool reported error", latency_ms=latency,
                              injection_flags=flags)
        return ToolResult(ok=True, content=text, structured=getattr(res, "structured_content", None),
                          latency_ms=latency, truncated=truncated, injection_flags=flags)

    async def health_check(self) -> dict[str, Any]:
        if self._session is None:
            return {"status": "disconnected"}
        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(self._session.send_ping(), timeout=5)
            return {"status": "ok", "latency_ms": round((time.perf_counter() - t0) * 1000, 2), "server": self.server_info}
        except Exception as exc:  # noqa: BLE001
            return {"status": "unhealthy", "detail": str(exc)[:200]}

    async def shutdown(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            except Exception as exc:  # noqa: BLE001
                log.debug("mcp_session_task_error: %s", exc)
        self._task = None
        self._session = None

    @property
    def connected(self) -> bool:
        return self._session is not None


class McpStdioAdapter(_SessionAdapter):
    transport = "stdio"

    def __init__(self, server_id: str, name: str, command: str, args: list[str], env: dict[str, str] | None = None,
                 **kw: Any) -> None:
        super().__init__(server_id, name, **kw)
        self.command = _resolve_command(command)
        self.args = args
        # Minimal environment: never leak the platform's own secrets into third-party processes.
        base_env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR") if k in os.environ}
        self.env = {**base_env, **(env or {})}

    def _transport(self):
        params = StdioServerParameters(command=self.command, args=self.args, env=self.env)
        return stdio_client(params, errlog=open(os.devnull, "w"))  # noqa: SIM115 - owned by transport lifetime

    async def authenticate(self) -> None:
        # STDIO servers inherit credentials only via the explicit env mapping above.
        return None


class McpHttpAdapter(_SessionAdapter):
    transport = "streamable_http"

    def __init__(self, server_id: str, name: str, url: str, auth_type: str = "none",
                 credentials: dict[str, Any] | None = None, **kw: Any) -> None:
        super().__init__(server_id, name, **kw)
        self.url = url
        self.auth_type = auth_type
        self.credentials = credentials or {}
        self._headers: dict[str, str] = {}

    async def authenticate(self) -> None:
        c = self.credentials
        if self.auth_type == "api_key":
            header = c.get("header", "Authorization")
            key = c.get("api_key", "")
            self._headers = {header: f"Bearer {key}" if header.lower() == "authorization" else key}
        elif self.auth_type == "oauth2":
            # OAuth 2.0 client-credentials grant
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(c["token_url"], data={"grant_type": "client_credentials",
                                                            "client_id": c["client_id"],
                                                            "client_secret": c["client_secret"],
                                                            **({"scope": c["scope"]} if c.get("scope") else {})})
                if r.status_code >= 400:
                    raise AdapterError(f"OAuth token request failed: HTTP {r.status_code}")
                self._headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        elif self.auth_type == "service":
            self._headers = {str(k): str(v) for k, v in (c.get("headers") or {}).items()}

    def _transport(self):
        client = httpx2.AsyncClient(headers=self._headers, timeout=httpx2.Timeout(self.timeout_s, read=300))
        return _OwnedClientTransport(self.url, client)

    async def initialize(self) -> None:
        await self.authenticate()
        await super().initialize()


class _OwnedClientTransport:
    """Wraps streamable_http_client so the httpx2 client is closed with the transport."""

    def __init__(self, url: str, client: httpx2.AsyncClient) -> None:
        self.url, self.client = url, client
        self._cm = None

    async def __aenter__(self):
        await self.client.__aenter__()
        self._cm = streamable_http_client(self.url, http_client=self.client)
        return await self._cm.__aenter__()

    async def __aexit__(self, *exc):
        try:
            await self._cm.__aexit__(*exc)
        finally:
            await self.client.__aexit__(None, None, None)
