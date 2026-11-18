# API reference

Interactive docs: `http://localhost:8100/docs` (Swagger UI) · machine-readable: [`openapi.json`](openapi.json) (generated from the running app).

Conventions: all endpoints under `/api/v1` require `Authorization: Bearer <jwt>` or `X-API-Key` unless noted; list endpoints accept `limit` (≤200) and `offset` and return `{items,total,limit,offset}`; errors use `{"error":{"code","message","details"},"request_id"}`; `429`/`503` include `Retry-After`; `POST /agents/{id}/execute` honours `Idempotency-Key`.

## auth

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/v1/auth/login` | Authenticate with email/password and receive access + refresh tokens |
| `POST` | `/api/v1/auth/logout` | Logout |
| `GET` | `/api/v1/auth/me` | Me |
| `POST` | `/api/v1/auth/refresh` | Rotate refresh token (reuse of a rotated token revokes the session family) |
| `GET` | `/api/v1/auth/sessions` | Active sessions (refresh-token families) for the current user |
| `DELETE` | `/api/v1/auth/sessions/{session_id}` | Revoke Session |

## security

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/audit/events` | Query the append-only audit trail |
| `GET` | `/api/v1/audit/summary` | Audit Summary |
| `GET` | `/api/v1/audit/verify` | Verify hash-chain integrity of this organization's audit log |
| `GET` | `/api/v1/security/api-keys` | List Keys |
| `POST` | `/api/v1/security/api-keys` | Create an API key (shown once; stored as SHA-256 hash) |
| `DELETE` | `/api/v1/security/api-keys/{key_id}` | Revoke Key |
| `GET` | `/api/v1/security/permissions` | List Permissions |
| `GET` | `/api/v1/security/roles` | List Roles |
| `GET` | `/api/v1/security/teams` | List Teams |
| `POST` | `/api/v1/security/teams` | Create Team |
| `GET` | `/api/v1/security/users` | List Users |
| `POST` | `/api/v1/security/users` | Create User |
| `PUT` | `/api/v1/security/users/{user_id}` | Update User Roles |

## agents

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/agents` | List Agents |
| `POST` | `/api/v1/agents` | Create an agent (version 1, status draft) |
| `GET` | `/api/v1/agents/{agent_id}` | Get Agent |
| `PUT` | `/api/v1/agents/{agent_id}` | Update agent; config changes create a new immutable version |
| `POST` | `/api/v1/agents/{agent_id}/clone` | Clone Agent |
| `POST` | `/api/v1/agents/{agent_id}/execute` | Execute an agent (sync waits; async returns 202). Honors Idempotency-Key. |
| `GET` | `/api/v1/agents/{agent_id}/executions` | List Executions |
| `POST` | `/api/v1/agents/{agent_id}/lifecycle` | Transition draft/active/inactive/archived |
| `GET` | `/api/v1/agents/{agent_id}/versions` | Versions |
| `GET` | `/api/v1/executions` | All Executions |
| `GET` | `/api/v1/executions/{execution_id}` | Get Execution |
| `POST` | `/api/v1/executions/{execution_id}/cancel` | Cancel Execution |

## providers

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/providers` | Built-in (env-configured) and organization-registered providers |
| `POST` | `/api/v1/providers` | Create Provider |
| `POST` | `/api/v1/providers/models` | Create Model |
| `POST` | `/api/v1/providers/playground` | Single completion against any provider (for connectivity checks) |
| `GET` | `/api/v1/providers/{name}/health` | Provider Health |
| `DELETE` | `/api/v1/providers/{provider_id}` | Delete Provider |

## mcp

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/mcp/servers` | List Servers |
| `POST` | `/api/v1/mcp/servers` | Register |
| `DELETE` | `/api/v1/mcp/servers/{server_id}` | Delete Server |
| `GET` | `/api/v1/mcp/servers/{server_id}` | Get Server |
| `PUT` | `/api/v1/mcp/servers/{server_id}` | Update server config (versioned); reconnect to apply |
| `POST` | `/api/v1/mcp/servers/{server_id}/connect` | Connect, authenticate and discover tools |
| `POST` | `/api/v1/mcp/servers/{server_id}/disconnect` | Disconnect |
| `POST` | `/api/v1/mcp/servers/{server_id}/health` | Health |
| `GET` | `/api/v1/mcp/servers/{server_id}/tools` | Server Tools |

## tools

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/tools` | Registry |
| `GET` | `/api/v1/tools/executions` | Tool Executions |
| `PUT` | `/api/v1/tools/{tool_id}` | Allowlist / risk / approval settings |
| `POST` | `/api/v1/tools/{tool_id}/execute` | Direct tool invocation (policy-checked, audited) |

## prompts

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/prompts` | List Templates |
| `POST` | `/api/v1/prompts` | Create Template |
| `GET` | `/api/v1/prompts/{template_id}` | Get Template |
| `POST` | `/api/v1/prompts/{template_id}/compare` | Side-by-side execution of two versions on identical inputs |
| `POST` | `/api/v1/prompts/{template_id}/preview` | Preview |
| `POST` | `/api/v1/prompts/{template_id}/test` | Run a prompt version with grounding/citation/structure/consistency checks |
| `POST` | `/api/v1/prompts/{template_id}/versions` | New Version |
| `POST` | `/api/v1/prompts/{template_id}/versions/{version}/review` | Approve/reject (separation of duties enforced) |
| `POST` | `/api/v1/prompts/{template_id}/versions/{version}/submit` | Submit |

## knowledge

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/v1/knowledge/answer` | Grounded answer with citations verified against retrieved passages |
| `GET` | `/api/v1/knowledge/collections` | Collections |
| `GET` | `/api/v1/knowledge/documents` | List Documents |
| `POST` | `/api/v1/knowledge/documents` | Upload PDF/DOCX/TXT/MD/CSV; extract, chunk, embed, index |
| `DELETE` | `/api/v1/knowledge/documents/{doc_id}` | Delete |
| `GET` | `/api/v1/knowledge/documents/{doc_id}/chunks` | Inspect chunks with offsets (provenance) |
| `POST` | `/api/v1/knowledge/search` | Retrieval with per-strategy score breakdown (debugging interface) |

## evaluations

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/evaluations/datasets` | List Datasets |
| `POST` | `/api/v1/evaluations/datasets` | Create Dataset |
| `GET` | `/api/v1/evaluations/datasets/{dataset_id}` | Get Dataset |
| `POST` | `/api/v1/evaluations/datasets/{dataset_id}/cases` | Add Case |
| `DELETE` | `/api/v1/evaluations/datasets/{dataset_id}/cases/{case_id}` | Delete Case |
| `POST` | `/api/v1/evaluations/run` | Run an evaluation in the background |
| `GET` | `/api/v1/evaluations/runs` | List Runs |
| `GET` | `/api/v1/evaluations/{run_id}` | Get Run |
| `GET` | `/api/v1/evaluations/{run_id}/results` | Per-case results + report |
| `POST` | `/api/v1/redteam/run` | Run the adversarial suite in the sandbox |
| `GET` | `/api/v1/redteam/runs` | Redteam Runs |
| `GET` | `/api/v1/redteam/scenarios` | Scenarios |

## experiments

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/experiments` | List Experiments |
| `POST` | `/api/v1/experiments` | Create |
| `GET` | `/api/v1/experiments/{experiment_id}` | Get |
| `GET` | `/api/v1/experiments/{experiment_id}/assign/{unit_id}` | Deterministic traffic assignment for a unit (user/session) |
| `GET` | `/api/v1/experiments/{experiment_id}/results` | Results |
| `POST` | `/api/v1/experiments/{experiment_id}/run` | Execute variants over the attached dataset |

## workflows

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/workflow-executions/{execution_id}` | Get Execution |
| `POST` | `/api/v1/workflow-executions/{execution_id}/approve` | Approve or reject a human-approval step |
| `POST` | `/api/v1/workflow-executions/{execution_id}/cancel` | Cancel |
| `POST` | `/api/v1/workflow-executions/{execution_id}/resume` | Resume from a breakpoint (step=true executes one node) |
| `GET` | `/api/v1/workflows` | List Workflows |
| `POST` | `/api/v1/workflows` | Create |
| `POST` | `/api/v1/workflows/validate` | Validate a graph without saving |
| `GET` | `/api/v1/workflows/{workflow_id}` | Get |
| `PUT` | `/api/v1/workflows/{workflow_id}` | Save a new version |
| `POST` | `/api/v1/workflows/{workflow_id}/execute` | Execute (optionally with breakpoints / step mode) |
| `GET` | `/api/v1/workflows/{workflow_id}/executions` | Executions |

## engineering

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/architecture/adrs` | List Adrs |
| `POST` | `/api/v1/architecture/adrs` | Create Adr |
| `PUT` | `/api/v1/architecture/adrs/{adr_id}` | Update Adr |
| `GET` | `/api/v1/architecture/designs` | List Designs |
| `POST` | `/api/v1/architecture/designs` | Create Design |
| `GET` | `/api/v1/architecture/designs/{design_id}` | Get Design |
| `PUT` | `/api/v1/architecture/designs/{design_id}` | Update Design |
| `POST` | `/api/v1/architecture/designs/{design_id}/review` | Run the Architecture Review Assistant |
| `POST` | `/api/v1/architecture/designs/{design_id}/sequence` | Generate a Mermaid sequence diagram from edges |
| `GET` | `/api/v1/code-review` | Code Reviews |
| `POST` | `/api/v1/code-review` | Static analysis (deterministic) + optional AI review (labelled) |

## platform

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/v1/dashboard/overview` | Live overview computed from persisted executions and measured telemetry |
| `GET` | `/api/v1/metrics` | Measured performance metrics (P50/P95/P99, RPS, errors) by route and operation |
| `POST` | `/api/v1/performance/loadtest` | Run a bounded synthetic load test against this API (real requests) |
| `GET` | `/api/v1/settings` | Settings |
| `PUT` | `/api/v1/settings/feature-flags` | Toggle feature flags at runtime (process-local; persist via env) |
| `GET` | `/api/v1/usage` | Token / cost / latency usage aggregated from persisted usage metrics |

## health

| Method | Path | Summary |
|---|---|---|
| `GET` | `/health` | Liveness |
| `GET` | `/ready` | Readiness: database, redis, migrations, MCP |
