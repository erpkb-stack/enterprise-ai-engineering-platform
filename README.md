# AI Engineering Command Center (AIECC)

An enterprise platform to **design, run, evaluate, secure and observe AI systems**: agents with bounded autonomy,
MCP servers and a policy-checked tool registry, versioned prompts, RAG with verified citations, an evaluation lab,
a sandboxed red-team lab, A/B experiments with statistical guard-rails, a visual workflow designer with human approval,
measured performance telemetry, RBAC + tamper-evident audit, an architecture review lab and a code review assistant.

Runs fully locally **without paid API keys** (deterministic mock provider, clearly labelled `MOCK`) and with real
models through **Ollama** or hosted providers (OpenAI, Anthropic, Gemini, NVIDIA NIM, any OpenAI-compatible endpoint).

> Brand colour: Roku purple `#1F004B` (sidebar, primary actions, login hero).

- Architecture: [`docs/architecture/overview.md`](docs/architecture/overview.md) · ADRs: [`docs/adr`](docs/adr)
- API: [`docs/api/README.md`](docs/api/README.md) (114 operations) · Swagger UI at `http://localhost:8100/docs`
- Security: [`docs/security/threat-model.md`](docs/security/threat-model.md) · Ops: [`docs/operations`](docs/operations)
- **Final engineering report: [`docs/ENGINEERING_REPORT.md`](docs/ENGINEERING_REPORT.md)**
- **Deploy to a VPS (Hostinger) with HTTPS: [`deploy/hostinger/README.md`](deploy/hostinger/README.md)**

---

## Quick start

### Prerequisites
Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 20+ (22 tested), Docker Desktop (for PostgreSQL/Redis). Optional: [Ollama](https://ollama.com) with `llama3.2:3b` (and `nomic-embed-text` for real embeddings). Keep ≥ 5 GB free disk for Docker.

### Option A — local processes (recommended for development)
```bash
make setup        # backend venv (uv) + frontend deps (npm); copies .env.example -> backend/.env
make infra        # PostgreSQL 16 + pgvector on :5442, Redis on :6390 (Docker)
make backend      # API on http://localhost:8100 — runs Alembic migrations, seeds DEMO data on first start
make frontend     # UI on http://localhost:5180 (new terminal)
```

### Option B — everything in Docker
```bash
make up           # postgres, redis, api (:8100), ARQ worker, nginx SPA (:8180)
make up-full      # + OTel collector, Prometheus (:9190), Grafana (:3190, "AIECC Overview" dashboard)
make down
```

### Option C — no Docker at all
```bash
make setup && make quickstart   # API on :8100 with SQLite + in-memory fallbacks (dev only)
make frontend
```

### Sign in (DEMO DATA)
All seeded users share the demo password **`Aiecc!Demo2026`** (`AIECC_DEMO_PASSWORD`, local/test only):

| User | Role |
|---|---|
| `admin@aiecc.local` | Platform Administrator |
| `engadmin@aiecc.local` | Engineering Administrator |
| `aieng@aiecc.local` | AI Engineer |
| `dev@aiecc.local` | Developer |
| `security@aiecc.local` | Security Reviewer |
| `auditor@aiecc.local` | Auditor |
| `viewer@aiecc.local` | Read-Only User |
| `admin@globex.local` | Admin of a second tenant (used to prove isolation) |

Then click **Run flagship research scenario** on the Overview page.

### Use a real model
- Local: `ollama pull llama3.2:3b` → set an agent's provider to `ollama` / model `llama3.2:3b` (Agents → Configuration),
  or pick `ollama` in Knowledge / Red Team / Code Review / Settings → Playground.
- Real embeddings: `ollama pull nomic-embed-text`, set `AIECC_EMBEDDING_PROVIDER=ollama`, `AIECC_EMBEDDING_MODEL=nomic-embed-text` and re-ingest documents.
- Hosted: set `AIECC_OPENAI_API_KEY` / `AIECC_ANTHROPIC_API_KEY` / `AIECC_GEMINI_API_KEY` / `AIECC_NVIDIA_NIM_API_KEY`,
  or register a provider in Settings (keys are encrypted at rest and never returned).

---

## The flagship scenario (what to demo)

1. A user asks: *"How quickly must a Sev2 incident be acknowledged, and what is the checkout service status?"*
2. The **Orchestrator** routes it (deterministic intent router, scores recorded) to the **Enterprise Research Agent**.
3. The agent retrieves documents via **hybrid RAG** (pgvector + full-text → RRF → rerank).
4. It discovers permitted tools; the **policy engine** authorizes `aiecc-demo-ops/get_service_status`.
5. The **MCP adapter** calls the demo MCP server over STDIO (pooled session, timeout, circuit breaker); output is sanitized and fenced as untrusted.
6. The model produces an answer with `[n]` citations; the **verifier** checks each citation against the passage actually retrieved.
7. **Audit** records the execution and tool calls (hash-chained); **OTel** spans and **Prometheus** metrics capture latency; the **dashboard** updates live via WebSocket.

---

## Modules

| Module | Highlights |
|---|---|
| Agents | 7 types, create/edit/clone/version/activate/deactivate/archive, visual tool/collaborator builder (React Flow), retries, fallbacks, timeouts, memory window, cancel, execution trace |
| MCP & Tools | STDIO + Streamable HTTP, API key / OAuth2 client-credentials / service auth, discovery (tools + resources), health, versioned config, deny-by-default allowlist, risk & approval, invocation log with latency/error rate |
| Prompt Studio | system/developer/user separation, Jinja variables (sandboxed), token estimates, Monaco editing, versions, test bench (grounding, citations, structure, adherence, consistency, leakage), side-by-side compare, approval with separation of duties |
| RAG | PDF/DOCX/TXT/MD/CSV, configurable chunking, pgvector HNSW, keyword FTS, hybrid RRF, rerank, context compression (flagged), metadata filters, provenance and a retrieval debugger |
| Evaluation | golden datasets, 14 deterministic metrics + optional LLM judge (with stated limitations), background runs, regression vs baseline |
| Red Team | 10 sandboxed scenarios (direct/indirect injection, jailbreak, leakage, unauthorized/out-of-scope tools, malicious tool output, retrieval poisoning, cross-tenant, autonomy), canary secrets, "platform control" vs "model behaviour" labelling |
| Experiments | prompt/model/retrieval/agent dimensions, sticky hash allocation, paired or split runs, t-based CIs, Welch's test, Wilson intervals, minimum-sample guard |
| Workflows | 12 node types, drag-and-drop designer, validation, versions, execution trace, breakpoints/step, human approval, retries, error handlers, SSRF-safe API node |
| Performance | measured P50/P95/P99, RPS, error rate per route & operation, concurrency/backpressure, circuit breakers, CPU/memory, synthetic-tagged load runner, Locust |
| Security & Audit | JWT + rotating refresh tokens, lockout, RBAC (7 roles/36 permissions), API keys, sessions, tenant isolation, append-only hash-chained audit with verification |
| Architecture Lab | component canvas, review engine (8 quality attributes), tradeoff report, Mermaid sequence diagrams, data flow / API contracts, ADRs |
| Code Review | AST analysis (blocking I/O in async, missing await, fire-and-forget tasks, leaks, eval/pickle/shell/SQL injection, secrets, timeouts, complexity) separated from AI suggestions and generated tests |

---

## Testing

| Command | What |
|---|---|
| `make test-backend` | 120 pytest tests on SQLite (unit, integration, security, AI regression) |
| `make test-pg` | same suite on PostgreSQL + pgvector + Alembic (121, incl. DB trigger tests) |
| `make test-ollama` | grounded answer with a real local model |
| `make test-frontend` | Vitest + Testing Library |
| `make e2e` | Playwright against a running stack (`E2E_BASE_URL=http://localhost:8180` for Docker) |
| `make lint` / `make audit` | Ruff (incl. bandit-style `S` and `ASYNC` rules), TypeScript; pip-audit, npm audit |
| `make loadtest` | Locust: 50 users × 2 min |

## Repository layout
```
backend/   FastAPI app (api, core, domain, services, agents, rag, evaluations, experiments, workflows,
           architecture, codereview, integrations/{llm,mcp,tools,vector_store}, security, observability, models, seed),
           Alembic migrations, tests
frontend/  React 19 + TS + Vite + Tailwind v4 + shadcn-style UI, React Query, Zustand, React Flow, Recharts, Monaco, Mermaid
infrastructure/  nginx, Prometheus (+alerts), Grafana provisioning, OTel collector, Kubernetes manifests
docs/      architecture, ADRs, API, security, operations, engineering report
scripts/   setup, Locust workload
```

## Ports
API `8100` · UI dev `5180` · UI Docker `8180` · Postgres `5442` · Redis `6390` · Prometheus `9190` · Grafana `3190` · OTLP `4318`

Troubleshooting: [`docs/operations/troubleshooting.md`](docs/operations/troubleshooting.md).
