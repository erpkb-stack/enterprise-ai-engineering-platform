"""LLM Gateway: provider factory + registry, model routing with fallbacks, retries, circuit breakers,
token/cost accounting and telemetry. This is the only entry point business code uses for LLM calls."""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import CircuitOpenError, UpstreamError
from app.core.logging import get_logger
from app.core.resilience import RetryPolicy, breakers, retry_async
from app.integrations.llm.base import (
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    ProviderUnavailable,
    estimate_cost,
    pricing_for,
)
from app.integrations.llm.http_providers import (
    AnthropicProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    RetryableHTTPError,
)
from app.integrations.llm.mock import MockProvider
from app.models import ModelConfiguration, ModelProvider, UsageMetric
from app.observability import metrics
from app.observability.tracing import span
from app.security.crypto import decrypt_secret

log = get_logger(__name__)

PROVIDER_KINDS = ["mock", "ollama", "openai", "anthropic", "gemini", "nvidia_nim", "openai_compatible"]


def build_provider(kind: str, name: str, base_url: str | None, api_key: str | None, timeout_s: float) -> LLMProvider:
    s = get_settings()
    match kind:
        case "mock":
            return MockProvider(name)
        case "ollama":
            return OllamaProvider(name, base_url or s.ollama_base_url, timeout_s=timeout_s)
        case "openai":
            return OpenAICompatibleProvider(name, base_url or s.openai_base_url, api_key, timeout_s, kind="openai",
                                            supports_developer_role=True)
        case "nvidia_nim":
            return OpenAICompatibleProvider(name, base_url or s.nvidia_nim_base_url, api_key, timeout_s, kind="nvidia_nim")
        case "openai_compatible":
            if not base_url:
                raise ProviderUnavailable("openai_compatible provider requires base_url")
            return OpenAICompatibleProvider(name, base_url, api_key, timeout_s)
        case "anthropic":
            return AnthropicProvider(name, base_url or "https://api.anthropic.com", api_key, timeout_s)
        case "gemini":
            return GeminiProvider(name, base_url or "https://generativelanguage.googleapis.com/v1beta", api_key, timeout_s)
    raise ProviderUnavailable(f"Unknown provider kind '{kind}'")


@dataclass
class Route:
    provider: str
    model: str


class LLMGateway:
    def __init__(self) -> None:
        self._builtin: dict[str, LLMProvider] = {}
        self._org_cache: dict[str, tuple[str, LLMProvider]] = {}

    def _init_builtin(self) -> None:
        if self._builtin:
            return
        s = get_settings()
        t = s.llm_timeout_s
        sec = lambda v: v.get_secret_value() if v else None  # noqa: E731
        self._builtin = {
            "mock": build_provider("mock", "mock", None, None, t),
            "ollama": build_provider("ollama", "ollama", s.ollama_base_url, None, t),
            "openai": build_provider("openai", "openai", s.openai_base_url, sec(s.openai_api_key), t),
            "anthropic": build_provider("anthropic", "anthropic", None, sec(s.anthropic_api_key), t),
            "gemini": build_provider("gemini", "gemini", None, sec(s.gemini_api_key), t),
            "nvidia_nim": build_provider("nvidia_nim", "nvidia_nim", s.nvidia_nim_base_url, sec(s.nvidia_nim_api_key), t),
        }

    def builtin_names(self) -> list[str]:
        self._init_builtin()
        return list(self._builtin)

    async def resolve(self, db: AsyncSession | None, org_id: str | None, name: str) -> LLMProvider:
        self._init_builtin()
        if db is not None and org_id:
            row = (
                await db.execute(select(ModelProvider).where(ModelProvider.organization_id == org_id, ModelProvider.name == name))
            ).scalar_one_or_none()
            if row is not None:
                if not row.is_enabled:
                    raise ProviderUnavailable(f"Provider '{name}' is disabled")
                fingerprint = f"{row.kind}|{row.base_url}|{row.credential_encrypted}|{row.config}"
                cached = self._org_cache.get(row.id)
                if cached and cached[0] == fingerprint:
                    return cached[1]
                if row.kind in self._builtin and not row.base_url and not row.credential_encrypted:
                    prov = self._builtin[row.kind]
                else:
                    prov = build_provider(row.kind, row.name, row.base_url, decrypt_secret(row.credential_encrypted),
                                          get_settings().llm_timeout_s)
                self._org_cache[row.id] = (fingerprint, prov)
                return prov
        if name in self._builtin:
            return self._builtin[name]
        raise ProviderUnavailable(f"Unknown provider '{name}'")

    async def _pricing(self, db: AsyncSession | None, org_id: str | None, model: str) -> tuple[float, float]:
        if db is not None and org_id:
            mc = (
                await db.execute(select(ModelConfiguration).where(ModelConfiguration.organization_id == org_id,
                                                                  ModelConfiguration.model_name == model).limit(1))
            ).scalar_one_or_none()
            if mc is not None:
                return mc.input_cost_per_1k, mc.output_cost_per_1k
        return pricing_for(model)

    async def complete(
        self,
        req: CompletionRequest,
        *,
        route: Route,
        fallbacks: list[Route] | None = None,
        db: AsyncSession | None = None,
        org_id: str | None = None,
        purpose: str = "completion",
        max_attempts: int = 3,
        synthetic: bool = False,
    ) -> tuple[CompletionResponse, dict[str, Any]]:
        """Returns (response, meta). meta records attempts, fallbacks used and cost."""
        chain = [route, *(fallbacks or [])]
        attempts: list[dict[str, Any]] = []
        last_exc: Exception | None = None
        for r in chain:
            breaker = breakers.get(f"llm:{r.provider}", failure_threshold=5, reset_timeout_s=30)
            try:
                provider = await self.resolve(db, org_id, r.provider)
            except ProviderUnavailable as exc:
                attempts.append({"provider": r.provider, "model": r.model, "error": str(exc)})
                last_exc = exc
                continue
            policy = RetryPolicy(max_attempts=max_attempts, base_delay_s=0.3,
                                 retry_on=(RetryableHTTPError, httpx.TransportError, TimeoutError),
                                 no_retry_on=(ProviderUnavailable, CircuitOpenError))
            t0 = time.perf_counter()
            r_req = req.model_copy(update={"model": r.model})
            try:
                with span("llm.complete", provider=r.provider, model=r.model, purpose=purpose):
                    resp = await retry_async(
                        lambda p=provider, rq=r_req, b=breaker: b.call(lambda: p.complete(rq)),
                        policy,
                        on_retry=lambda n, e, rr=r: (metrics.record_retry("llm", rr.provider),
                                                     attempts.append({"provider": rr.provider, "retry": n, "error": str(e)[:200]})),
                    )
            except Exception as exc:  # noqa: BLE001
                latency = (time.perf_counter() - t0) * 1000
                metrics.record_op("llm", f"{r.provider}/{r.model}", latency, False, synthetic)
                attempts.append({"provider": r.provider, "model": r.model, "error": str(exc)[:300]})
                await self._usage(db, org_id, r, latency, 0, 0, 0.0, False, provider.is_mock, synthetic, purpose)
                last_exc = exc
                continue
            pin, pout = await self._pricing(db, org_id, r.model)
            cost = 0.0 if provider.is_mock else estimate_cost(resp.tokens_in, resp.tokens_out, pin, pout)
            metrics.record_op("llm", f"{r.provider}/{r.model}", resp.latency_ms, True, synthetic)
            metrics.record_tokens(r.provider, r.model, resp.tokens_in, resp.tokens_out, cost)
            await self._usage(db, org_id, r, resp.latency_ms, resp.tokens_in, resp.tokens_out, cost, True,
                              provider.is_mock, synthetic, purpose)
            return resp, {"attempts": attempts, "fallback_used": r is not route, "cost_usd": cost,
                          "provider": r.provider, "model": r.model, "is_mock": provider.is_mock}
        raise UpstreamError(f"All model routes failed: {last_exc}", details={"attempts": attempts})

    async def embed(self, texts: list[str], *, db: AsyncSession | None = None, org_id: str | None = None,
                    provider_name: str | None = None, model: str | None = None) -> tuple[list[list[float]], str, bool]:
        s = get_settings()
        pname = provider_name or s.embedding_provider
        mname = model or s.embedding_model
        provider = await self.resolve(db, org_id, pname)
        t0 = time.perf_counter()
        breaker = breakers.get(f"embed:{pname}", failure_threshold=5, reset_timeout_s=30)
        out: list[list[float]] = []
        try:
            for i in range(0, len(texts), 64):  # request batching
                out.extend(await breaker.call(lambda b=texts[i : i + 64]: provider.embed(b, mname, s.embedding_dim)))
        except Exception:
            metrics.record_op("embedding", pname, (time.perf_counter() - t0) * 1000, False)
            raise
        metrics.record_op("embedding", pname, (time.perf_counter() - t0) * 1000, True)
        return out, f"{pname}/{mname}", provider.is_mock

    async def _usage(self, db, org_id, r: Route, latency, tin, tout, cost, ok, is_mock, synthetic, purpose) -> None:
        if db is None or not org_id:
            return
        db.add(UsageMetric(organization_id=org_id, kind="llm", name=purpose, provider=r.provider, model=r.model,
                           latency_ms=latency, tokens_in=tin, tokens_out=tout, cost_usd=cost, success=ok,
                           is_mock=is_mock, is_synthetic=synthetic))

    async def aclose(self) -> None:
        for p in list(self._builtin.values()) + [p for _, p in self._org_cache.values()]:
            with contextlib.suppress(Exception):
                await p.aclose()
        self._builtin.clear()
        self._org_cache.clear()


gateway = LLMGateway()
