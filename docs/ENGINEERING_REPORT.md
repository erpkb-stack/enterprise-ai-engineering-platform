# AIECC — Final Engineering Report

Date: 2026-11-18 · Scope: full implementation of the AIECC specification (modules A–K), local-first and cloud-ready.

## 1. Definition of Done — verification status

| Criterion | Status | Evidence |
|---|---|---|
| Frontend starts | ✅ | Vite dev server (:5180) and nginx image (:8180); all 16 pages exercised in the browser, light + dark themes |
| Backend starts | ✅ | Local (`make backend`) and container (`aiecc-backend`, healthy) |
| PostgreSQL migrations execute | ✅ | `alembic upgrade head` → revision `0001`, 38 tables, pgvector 0.8.7, HNSW/GIN indexes, audit triggers |
| Redis connectivity | ✅ | `/ready` → `redis: ok`; rate limits/idempotency/ARQ queue use it (graceful in-memory fallback otherwise) |
| Authentication | ✅ | login, lockout, refresh rotation + reuse detection, logout, sessions, API keys (tests/security) |
| Agent creation & execution | ✅ | lifecycle/versioning/clone tests; flagship orchestrator → research agent run via UI, API, Docker and Playwright |
| MCP discovery with a test server | ✅ | demo MCP server over STDIO **and** Streamable HTTP with API-key auth (wrong key → 502) |
| Tool permissions enforced | ✅ | 7-rule policy engine; deny-by-default discovery; agent scope; high-risk approval; schema validation |
| Prompt testing | ✅ | preview, test bench with grounding/structure/adherence/consistency/leakage, compare, approval + SoD |
| RAG returns source references | ✅ | chunk id, document, ordinal, char offsets, per-retriever scores for 4 strategies |
| Citation verification | ✅ | per-claim verification incl. misattributed / non-existent / unsupported citations |
| Evaluation reports | ✅ | RAG, prompt and agent targets; summary, per-case scores, LLM-judge (separate), baseline regression |
| Red-team in safe sandbox | ✅ | 10 scenarios with canary secrets; all 5 platform-control scenarios defended |
| Workflow execution | ✅ | branch + human approval + MCP tool + error handler + breakpoints/step + SSRF block |
| Audit events persisted | ✅ | hash chain verified; tamper detection (SQLite) and DB trigger rejection (PostgreSQL) tested |
| Metrics from actual requests | ✅ | rolling window + Prometheus scraped by Prometheus container; synthetic traffic tagged |
| Tests pass | ✅ | see §6 |
| Docker Compose starts the stack | ✅ | `--profile full`: postgres, redis, backend, ARQ worker, nginx SPA, OTel collector (receiving spans), Prometheus (target up), Grafana (dashboard provisioned) |

## 2. Implemented features (by module)
See README "Modules" table. Every UI action is wired to a working endpoint; there are no placeholder buttons.
AI responses are either real provider output or the deterministic mock provider, which is labelled **MOCK** everywhere
(API field `is_mock`, UI badges, evaluation/red-team caveats).

## 3. Architecture decisions
| ADR | Decision |
|---|---|
| [0001](adr/0001-modular-monolith.md) | Modular monolith with adapter seams for later extraction |
| [0002](adr/0002-provider-adapters.md) | Provider-agnostic LLM gateway, retries, breakers, fallbacks, cost accounting |
| [0003](adr/0003-postgres-pgvector.md) | PostgreSQL + pgvector as system of record; SQLite only for tests/quickstart |
| [0004](adr/0004-bounded-agent-runtime.md) | Bounded ReAct runtime with a provider-neutral JSON action protocol |
| [0005](adr/0005-mcp-session-ownership.md) | One long-lived session task per MCP server (anyio scope ownership, pooling) |
| [0006](adr/0006-deterministic-grounding.md) | Deterministic citation verification first; LLM judge separate |
| [0007](adr/0007-security-model.md) | RBAC + policy engine + hash-chained append-only audit |
| [0008](adr/0008-measured-telemetry.md) | Measured telemetry only; unspoofable synthetic tagging |
| [0009](adr/0009-workflow-engine.md) | Resumable DAG engine with dead-path elimination |
| [0010](adr/0010-no-kafka-no-k8s-yet.md) | Compose profiles now; K8s manifests ready; no Kafka without need |

## 4. Technology choices
Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic, asyncpg, pgvector, httpx, Redis, ARQ,
MCP Python SDK 2.x, OpenTelemetry, prometheus-client, Argon2, PyJWT, cryptography (Fernet), Jinja2 sandbox, pypdf,
python-docx, jsonschema, psutil. Frontend: React 19, TypeScript 7, Vite 8, Tailwind v4, shadcn-style Radix components,
TanStack Query, Zustand, React Flow (@xyflow/react 12), Recharts 3, Monaco (bundled locally), Mermaid, sonner.
Infra: Docker Compose, nginx, PostgreSQL 16 (pgvector), Redis 7.4, OTel Collector, Prometheus, Grafana; K8s manifests.
Celery was not used: ARQ is asyncio-native and fits the stack (ADR-0001).

## 5. API endpoints, schema & security controls
- 114 operations across 14 groups: [`api/README.md`](api/README.md), [`api/openapi.json`](api/openapi.json).
- Schema: 38 tables, ER diagram and index notes in [`architecture/overview.md`](architecture/overview.md#4-data-model-38-tables-alembic-0001).
- Security controls & their tests: [`security/threat-model.md`](security/threat-model.md).

## 6. Test results (all executed on 2026-11-18)

| Suite | Result |
|---|---|
| Backend on SQLite (`make test-backend`) | **120 passed**, 2 skipped (PostgreSQL-only trigger test; Ollama test) |
| Backend on PostgreSQL + pgvector + Alembic (`make test-pg`) | **121 passed**, 1 skipped (Ollama test) |
| Real model via Ollama `llama3.2:3b` (`make test-ollama`) | **1 passed** (non-mock grounded answer containing the reference fact) |
| Frontend unit (Vitest + RTL) | **11 passed** (formatters, API client incl. refresh rotation, trust components, login error state) |
| Playwright E2E against the Docker stack | **2 passed** (flagship scenario + citations + audit-chain verification; RBAC read-only) |
| Ruff (`E,F,W,I,B,UP,ASYNC,S`) / TypeScript strict | clean / clean |
| pip-audit | no known vulnerabilities |
| npm audit (prod) | 0 after overriding transitive `dompurify` → 3.4.18 (advisories GHSA-p98j-92pf-mc4p et al. via monaco-editor/mermaid) |

Backend test inventory: unit (resilience, safe-eval, crypto/JWT, chunking, statistics, action parsing, grounding,
scorers, policy matrix, sanitizer, mock provider, static analyzer, architecture rules, workflow validation, audit
scrubbing), integration (flagship scenario, lifecycle, bounds, timeout, cancellation, backpressure, idempotency,
MCP STDIO/HTTP, malicious output, all 5 upload formats, 4 retrieval strategies, metadata filters, prompts, evaluations
×3 targets, red team, experiments, workflows ×5, architecture, code review, metrics + load runner, dashboard, OpenAPI,
WebSocket auth), security (auth, lockout, rotation, RBAC matrix, tenant isolation ×2, secret exposure, API-key scope,
headers, rate limiting, audit tamper/trigger), AI (golden retrieval ×6, grounded answers ×6, fabricated citations,
unanswerable, injection ×2, Ollama).

## 7. Performance results (measured)

**Environment caveat:** measured on the development laptop while it was heavily oversubscribed — load average ≈ 280,
10.7 GB of 11.8 GB swap in use, disk at 97–100 %, with other projects' services (Kafka, ZooKeeper, other APIs) running.
Absolute numbers are therefore pessimistic; relative changes are meaningful. Raw CSVs: `scripts/loadtest/results/`.

Unloaded single-request latency (median of 5, 4-worker server, PostgreSQL in Docker):
`/health` 10 ms · `GET /agents` 78 ms · `GET /dashboard/overview` 133 ms.

Locust, 30 users, 0.2–1 s think time, 60 s, mixed workload (one API worker, PostgreSQL + Redis):

| Endpoint | Requests | P50 | P95 | P99 |
|---|---|---|---|---|
| GET agents/list | 471 | 460 ms | 1.4 s | 4.0 s |
| POST knowledge/search (hybrid + rerank) | 386 | 400 ms | 1.2 s | 4.5 s |
| GET dashboard/overview | 270 | 530 ms | 1.5 s | 4.1 s |
| POST agents/execute (orchestrated, RAG + MCP, mock LLM) | 70 | 1.9 s | 7.3 s | 10 s |
| **Aggregate** | **1,207 (21.6 rps)** | **480 ms** | **2.1 s** | **4.6 s** |

The 49 failures in that run were all HTTP 429 from the per-IP API rate limit (600/min) — the control behaving as designed.

**Bottlenecks found and fixed by the platform's own tooling** (first run → after fixes, same workload):
1. Argon2 password verification executed on the event loop (~50–100 ms CPU each), stalling all requests during logins
   → moved to `asyncio.to_thread`.
2. N+1 queries when listing agents → single batched version query.
3. 3–4 queries per request to resolve the principal → 15 s TTL identity cache with explicit invalidation on role change.

Result: throughput 442 → 1,207 requests per minute window (**2.7×**), aggregate P50 980 → 480 ms, `agents/list` P50
900 → 460 ms, `agents/execute` P50 4.4 → 1.9 s. A 4-worker run produced the same throughput (20.2 rps), confirming the
client's closed-loop think time and host saturation — not Python CPU — bound these numbers.

Other measured items: MCP STDIO cold start ≈ 6–11 s (process spawn + SDK import) vs ≈ 20–40 ms per call with the
warm pooled session; containerized research-agent execution 1.5 s; ARQ evaluation of 6 cases 1.5 s.

## 8. Known limitations
- **Mock provider is not a language model.** It is a transparent extractive stand-in for credential-free development;
  model-behaviour red-team scenarios and LLM-judge scores are only meaningful with Ollama or a hosted model.
- Citation verification is lexical; it under-credits faithful paraphrases (ADR-0006).
- Reranking uses a heuristic cross-scorer, not a neural cross-encoder (kept out to avoid ~500 MB of weights).
- OIDC login flow is configuration-ready but not implemented; MCP OAuth is client-credentials only.
- Refresh token stored in `sessionStorage` (XSS-readable; mitigated by CSP); httpOnly-cookie flow is a follow-up.
- Workflow branches execute sequentially; no parallel fan-out yet.
- In-process metrics window and MCP sessions are per replica (Prometheus aggregates; sessions are re-established per replica).
- Token counts are estimated when a provider does not return usage (labelled in the UI); costs use list prices.
- Feature-flag changes at runtime are process-local (persist via environment).
- SQLite quickstart mode creates schema from metadata and is dev-only.

## 9. Future improvements
1. Native tool-calling for providers that support it (fewer tokens/steps) behind the same runtime interface.
2. Neural reranker / NLI-based groundedness as optional strategies; semantic caching in the LLM gateway.
3. OIDC (auth-code + PKCE) and JWKS validation; httpOnly cookie sessions; per-tenant KMS keys.
4. Parallel workflow branches and durable workflow timers; workflow triggers (webhooks/schedules).
5. Distributed rate limiting with token buckets (Redis Lua) and per-tenant quotas/budgets for model spend.
6. CI pipeline (GitHub Actions) running lint, both test suites, Playwright, pip-audit/npm audit and image scanning.
7. Load testing on dedicated hardware with multiple API replicas and pgbouncer; publish capacity per replica.
