# ADR-0004: Bounded ReAct runtime with a JSON action protocol

- Status: Accepted

## Decision
Agents run a ReAct loop where each model turn must be one JSON object (`tool` or `final`). The runtime enforces:
max steps (≤ platform hard limit), total wall-clock budget, per-call timeout, retry/fallback, cooperative
cancellation, bounded concurrency with a rejecting queue (503 + Retry-After), context-window trimming,
delegation depth 1 for orchestrators, and policy evaluation for every tool call. Tool output is fenced as
`<untrusted_tool_output>` and scanned for injection patterns. Non-compliant model output gets one repair prompt.

## Consequences
+ Works identically on any provider; deterministic tests with the mock provider.
+ Runaway autonomy is impossible by construction (red-team scenario `autonomy-01`).
− Less efficient than native function calling on providers that support it (more tokens per step).
