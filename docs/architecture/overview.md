# AIECC Architecture

## 1. Context

```mermaid
flowchart LR
  user([Engineers / Reviewers / Auditors]) -->|HTTPS| web[React SPA<br/>Vite · React Flow · Monaco · Recharts]
  web -->|REST + WebSocket| api[AIECC API<br/>FastAPI modular monolith]
  api --> pg[(PostgreSQL 16<br/>+ pgvector)]
  api --> redis[(Redis<br/>rate limits · idempotency · ARQ queue)]
  worker[ARQ worker<br/>evaluations · red team] --> pg
  redis --> worker
  api -->|LLM gateway| llm{{Ollama · OpenAI · Anthropic · Gemini · NVIDIA NIM · OpenAI-compatible · Mock}}
  api -->|MCP STDIO / Streamable HTTP| mcp{{MCP servers}}
  api -->|OTLP| otel[OTel collector] 
  prom[Prometheus] -->|scrape /metrics| api
  graf[Grafana] --> prom
```

## 2. Layers (modular monolith, ADR-0001)

| Layer | Package | Responsibility |
|---|---|---|
| Presentation | `frontend/src` | SPA; RBAC-aware navigation; never holds secrets |
| API | `app/api/v1/*` | HTTP contracts (OpenAPI), validation (Pydantic v2), authN/authZ dependencies, pagination, idempotency |
| Application / use cases | `app/agents`, `app/rag`, `app/evaluations`, `app/experiments`, `app/workflows`, `app/architecture`, `app/codereview`, `app/services` | Orchestrate domain logic; transactional boundaries |
| Domain | `app/domain`, `app/security/policy.py`, `app/security/rbac.py` | Typed configs, lifecycle state machines, policy rules |
| Infrastructure | `app/core` (config, db, redis, resilience, jobs, logging), `app/models` | Persistence, pools, primitives |
| Integration / adapters | `app/integrations/{llm,mcp,tools,vector_store}` | Provider adapters, MCP client adapters, vector stores |
| Observability | `app/observability` | Prometheus metrics, rolling percentile window, OTel tracing, event bus |

Patterns: **Adapter** (LLM providers, tool adapters, vector stores), **Strategy** (retrieval strategies, rerankers, scorers),
**Factory** (`build_provider`, `build_adapter`), **Repository-style tenant scoping** (`get_owned`, `paginate`),
**Dependency injection** (FastAPI `Depends` for DB session and principal), **Event-driven** UI updates (in-process bus → WebSocket).

## 3. Flagship scenario: Enterprise Research Agent

```mermaid
sequenceDiagram
  autonumber
  actor U as User
  participant API as API (agents.execute)
  participant EX as BoundedExecutor
  participant O as Orchestrator runtime
  participant R as Research agent runtime
  participant GW as LLM Gateway
  participant PE as Policy engine
  participant KB as Builtin knowledge_search (RAG)
  participant M as MCP adapter (STDIO)
  participant V as Grounding verifier
  participant A as Audit (hash chain)
  U->>API: POST /agents/{orchestrator}/execute (Idempotency-Key)
  API->>EX: run (bounded concurrency, queue or 503)
  EX->>O: _run(execution)
  O->>O: deterministic intent router → Research agent (scores in trace)
  O->>R: delegate (depth 1)
  loop ≤ max_steps, within total budget
    R->>GW: complete(JSON action protocol) [timeout, retry, breaker, fallback]
    GW-->>R: {"action":"tool","tool":"knowledge_search"}
    R->>PE: evaluate_tool_call(principal, agent scope, schema)
    PE-->>R: allowed
    R->>KB: hybrid retrieval (pgvector + FTS → RRF → rerank)
    KB-->>R: passages [1..k] (fenced as untrusted)
    R->>PE: evaluate aiecc-demo-ops/get_service_status
    R->>M: call_tool (pooled session, timeout, breaker)
    M-->>R: sanitized result + injection flags
    R->>GW: complete
    GW-->>R: {"action":"final","answer":"… [1] …"}
  end
  R->>V: verify citations vs retrieved passages
  R->>A: agent.execute, tool.execute events (redacted)
  O-->>API: output + trace + citations + evaluation
  API-->>U: 200 (WebSocket streams step events meanwhile)
```

Telemetry: each LLM/tool/agent/retrieval call emits an OTel span, a Prometheus histogram observation and a
`usage_metrics` row (tokens, cost, latency, mock flag). The dashboard reads only those sources.

## 4. Data model (38 tables, Alembic `0001`)

```mermaid
erDiagram
  organizations ||--o{ users : has
  organizations ||--o{ teams : has
  users }o--o{ roles : user_roles
  roles }o--o{ permissions : role_permissions
  users ||--o{ refresh_tokens : sessions
  users ||--o{ api_keys : owns
  agents ||--o{ agent_versions : versions
  agents ||--o{ agent_executions : runs
  model_providers ||--o{ model_configurations : offers
  mcp_servers ||--o{ mcp_tools : exposes
  mcp_tools ||--o{ tool_executions : logs
  prompt_templates ||--o{ prompt_versions : versions
  evaluation_datasets ||--o{ evaluation_cases : contains
  evaluation_datasets ||--o{ evaluation_runs : evaluated_by
  evaluation_runs ||--o{ evaluation_results : produces
  experiments ||--o{ experiment_variants : arms
  experiment_variants ||--o{ experiment_observations : measures
  documents ||--o{ document_chunks : chunked_into
  document_chunks ||--|| embeddings : vector
  workflows ||--o{ workflow_versions : versions
  workflows ||--o{ workflow_executions : runs
  architecture_designs ||--o{ architecture_reviews : reviewed
  architecture_designs ||--o{ adrs : decisions
```

All tenant tables carry `organization_id` (FK, indexed). Notable indexes: HNSW on `embeddings.vector`
(cosine), GIN on `to_tsvector('english', document_chunks.content)`, GIN `jsonb_path_ops` on `documents.metadata`,
`(organization_id, created_at)` on executions/usage/audit, unique `(organization_id, chain_index)` on audit events.
`audit_events` has BEFORE UPDATE/DELETE/TRUNCATE triggers that raise.

## 5. Concurrency & resilience

| Concern | Mechanism | Where |
|---|---|---|
| Unbounded autonomy | `max_steps` ≤ hard limit, total budget, delegation depth 1 | `agents/runtime.py` |
| Overload | `BoundedExecutor` semaphore + bounded wait queue → 503 `Retry-After` | `core/resilience.py` |
| Slow / failing deps | per-call timeouts, jittered retries, circuit breakers (LLM, embeddings, each MCP server), fallback model routes | `integrations/llm/gateway.py`, `services/tool_service.py` |
| Abuse | fixed-window rate limiting (Redis or in-memory), stricter on `/auth/login`; account lockout | `api/middleware.py`, `api/v1/auth.py` |
| Duplicate submits | `Idempotency-Key` response replay (24 h) | `api/common.py` |
| Event-loop blocking | Argon2 offloaded with `asyncio.to_thread`; HTTP via pooled `httpx.AsyncClient` | `security/crypto.py` |
| Slow consumers | bounded per-subscriber WebSocket queues, drop-oldest | `observability/events.py` |
| Batching | embeddings sent in batches of 64 | `gateway.embed` |
| Graceful shutdown | stop intake → wait in-flight executions (10 s) → cancel → drain jobs → close MCP sessions, HTTP pools, Redis, DB | `main.py` lifespan |
| Cancellation | task registry; `POST /executions/{id}/cancel` → persisted `cancelled` | runtime |

## 6. Trust boundaries

1. Browser ↔ API: JWT bearer / API key; CORS allowlist; security headers; no tokens in URLs (WebSocket auth by first message).
2. API ↔ model providers: credentials server-side only (env or Fernet-encrypted rows); responses treated as untrusted text.
3. API ↔ MCP servers: allowlisted STDIO commands, minimal child env, authenticated HTTP transports, deny-by-default tools,
   sanitized + size-bounded outputs, policy engine before every call.
4. API ↔ external HTTP (workflow API nodes): hostname allowlist, private/loopback IP block, no redirects.
5. Tenant ↔ tenant: organization filter on every query; foreign IDs return 404 (tested).
