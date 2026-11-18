# ADR-0002: Provider-agnostic LLM gateway with adapters and fallbacks

- Status: Accepted

## Context
Teams must use OpenAI, Anthropic, Gemini, NVIDIA NIM, Ollama and arbitrary OpenAI-compatible endpoints, and the
platform must run with no paid credentials.

## Decision
A single `LLMGateway` owns routing, retries (jittered exponential backoff), per-provider circuit breakers, fallback
routes, token/cost accounting and telemetry. Providers implement `LLMProvider.complete/embed/list_models/health_check`.
Business code never imports a provider. A deterministic `MockProvider` (clearly flagged `is_mock`) enables
credential-free development and reproducible tests; Ollama is the default "real" local model.

## Consequences
+ Swapping or A/B-testing models is configuration, not code.
+ Every call is measured and attributable (usage_metrics, Prometheus, OTel spans).
− Lowest-common-denominator API: native tool-calling formats are not used; the agent runtime uses a JSON action
  protocol that works on any chat model (see ADR-0004).
