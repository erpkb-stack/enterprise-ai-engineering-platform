# ADR-0005: One long-lived session task per MCP server

- Status: Accepted

## Context
The MCP SDK transports are anyio context managers; their cancel scopes must exit in the task that entered them.
Opening a session per request is slow (STDIO process spawn ~1-6 s) and leaks processes on errors.

## Decision
`McpConnectionManager` keeps one adapter per server. Each adapter runs a dedicated "session task" that owns the
transport + `ClientSession`, signals readiness, and waits for a stop event. Request handlers share the session
(JSON-RPC multiplexing) behind a per-server semaphore. Servers are pre-warmed at startup; discovery marks new tools
**disabled** (deny-by-default) and auto-classifies destructive-looking names as high risk.
STDIO commands are allowlisted and spawned with a minimal environment (platform secrets never inherited).

## Consequences
+ Connection reuse (p50 tool call ~20-40 ms after warm-up vs seconds cold), clean shutdown.
− Session state lives in one API process; multiple replicas each hold their own sessions (acceptable; MCP is stateless per call here).
