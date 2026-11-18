# Agent runtime

Source: `backend/app/agents/runtime.py` · Decision: [ADR-0004](../adr/0004-bounded-agent-runtime.md)

## Lifecycle
`draft → active ⇄ inactive → archived` (archived is terminal and read-only). Every configuration change creates an
immutable `agent_versions` row; executions record the version they ran.

## Execution protocol
System prompt = protocol header (`AIECC_AGENT_PROTOCOL v1`) + tool catalogue (name, description, JSON schema) +
agent instructions. Each turn the model must return exactly one JSON object:

```json
{"thought": "…", "action": "tool", "tool": "aiecc-demo-ops/get_service_status", "arguments": {"service": "checkout"}}
{"thought": "…", "action": "final", "answer": "… [1] …"}
```

Tool results are returned as `OBSERVATION from <tool>: <untrusted_tool_output>…</untrusted_tool_output>`.
`knowledge_search` passages are renumbered globally across calls so `[n]` citations are unambiguous.

## Guarantees
| Guarantee | Mechanism |
|---|---|
| Bounded steps | `max_steps` validated ≤ `AIECC_MAX_AGENT_STEPS_HARD_LIMIT` |
| Bounded time | `asyncio.wait_for(total_timeout_s)`; per-call `timeout_s`; tool timeout |
| Bounded concurrency | `BoundedExecutor` (16 concurrent, 64 queued by default) → 503 when full |
| Resilience | retries with jitter, circuit breakers, fallback routes, one repair prompt for non-JSON output |
| Cancellation | task registry + `POST /executions/{id}/cancel` → status `cancelled` |
| Safety | policy engine per tool call; injection flags; deny-by-default tools; approval for high-risk tools |
| Memory | system + task + last N messages within a token budget |
| Delegation | orchestrator → specialist (depth 1) via deterministic intent router (scores recorded in trace) |
| Evaluation | post-run citation verification + groundedness, policy denials, injection flags stored on the execution |

## Statuses
`queued`, `running`, `succeeded`, `failed`, `timeout`, `max_steps_exceeded`, `cancelled`.
