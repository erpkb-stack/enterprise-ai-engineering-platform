# Operations runbook

## Health
- Liveness: `GET /health` (process up). Readiness: `GET /ready` (DB latency, migration revision, pgvector version,
  Redis, MCP sessions, executor saturation).
- Metrics: `GET /metrics` (Prometheus, internal only — nginx returns 404 publicly); JSON view at `/api/v1/metrics`.
- Traces: enable `AIECC_OTEL_ENABLED=true` and point `AIECC_OTEL_ENDPOINT` at an OTLP/HTTP collector.

## SLOs (suggested)
| SLI | Target | Alert (infrastructure/monitoring/prometheus/alerts.yml) |
|---|---|---|
| API p95 latency (organic) | < 1.5 s | `AieccHighP95Latency` |
| 5xx rate | < 2 % | `AieccErrorRate` |
| Execution queue depth | < 50 | `AieccExecutionQueueSaturated` |

## Common procedures
**Provider outage** — the breaker for `llm:<provider>` opens after 5 consecutive failures and half-opens after 30 s.
Add a fallback route to critical agents (Configuration → Fallback route) or switch the agent's provider.

**Suspected prompt-injection incident** — filter the audit trail by `action=tool.` and `severity=warning`
(injection flags), inspect the execution trace's `observation` blocks, disable the offending MCP tool in Tool Registry
(audited), and run the Red Team suite against the agent's model.

**Audit integrity check** — `GET /api/v1/audit/verify` (Auditor / Security Reviewer). A failure returns the first broken
`chain_index`; preserve the database snapshot and escalate.

**Rotate secrets** — `AIECC_JWT_SECRET` rotation invalidates all access tokens (users re-login via refresh tokens only if
those are also rotated — plan a maintenance window). `AIECC_ENCRYPTION_KEY` rotation requires re-encrypting
`mcp_servers.credential_encrypted` and `model_providers.credential_encrypted` (use `MultiFernet` with old+new keys).

**Scale** — API is stateless apart from per-process MCP sessions and the in-process metrics window; scale replicas
horizontally (HPA in `infrastructure/deployment/k8s/aiecc.yaml`), move background jobs to ARQ workers
(`AIECC_JOBS_MODE=arq`), and ensure Redis is shared (rate limits/idempotency).

**Backups** — PostgreSQL point-in-time recovery (WAL archiving) is the backup of record for all data incl. vectors.
