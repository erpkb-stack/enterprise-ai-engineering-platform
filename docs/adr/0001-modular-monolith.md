# ADR-0001: Modular monolith with extractable service boundaries

- Status: Accepted
- Date: 2026-11-17

## Context
AIECC spans 11 functional modules (agents, MCP, prompts, RAG, evaluation, red team, experiments, workflows,
observability, security, architecture lab, code review). The team is small, the domain is still evolving, and
most use cases are transactional across modules (e.g. an agent execution writes executions, tool executions,
usage metrics and audit events atomically).

## Decision
Ship one deployable FastAPI application with strict internal layering:
`api` (HTTP, validation, authZ) → `services`/domain modules (use cases) → `integrations` (LLM, MCP, vector store adapters)
→ `models` (persistence). Background work runs in the same codebase via ARQ workers (separate process, same image).

## Consequences
+ One transaction boundary for audit integrity; simple local dev and deployment.
+ Adapters (`LLMProvider`, `ToolAdapter`, `VectorStore`) are the seams for future extraction (e.g. an AI gateway
  service or an MCP proxy) without touching business logic.
− Module boundaries are enforced by convention/review, not process isolation.
− A heavy module (evaluation runs) shares the API's resources; mitigated by running jobs in ARQ workers.

## Alternatives considered
- Microservices per module: rejected — distributed transactions for audit, more ops burden, no scaling need yet.
- Kafka/event bus: rejected — no cross-team consumers; Redis-backed ARQ covers async work. Revisit when >1 consumer.
