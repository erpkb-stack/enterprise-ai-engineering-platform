"""Real provider adapters over pooled httpx clients.

Each adapter translates the common CompletionRequest into the provider wire format.
No provider-specific logic leaks outside this module.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.integrations.llm.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    ProviderUnavailable,
    estimate_tokens,
    messages_tokens,
)

_LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=30)


class RetryableHTTPError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code == 429 or resp.status_code >= 500:
        raise RetryableHTTPError(resp.status_code, resp.text)
    if resp.status_code >= 400:
        raise ProviderUnavailable(f"HTTP {resp.status_code}: {resp.text[:300]}")


class _HttpProvider(LLMProvider):
    def __init__(self, name: str, base_url: str, api_key: str | None = None, timeout_s: float = 60.0) -> None:
        super().__init__(name)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(timeout_s, connect=5.0), limits=_LIMITS)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _need_key(self) -> None:
        if not self.api_key:
            raise ProviderUnavailable(f"Provider '{self.name}' has no API key configured")


class OllamaProvider(_HttpProvider):
    kind = "ollama"

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        t0 = time.perf_counter()
        msgs = [{"role": "system" if m.role == "developer" else ("user" if m.role == "tool" else m.role),
                 "content": m.content} for m in req.messages]
        body: dict[str, Any] = {
            "model": req.model, "messages": msgs, "stream": False,
            "options": {"temperature": req.temperature, "num_predict": req.max_tokens, **({"seed": req.seed} if req.seed is not None else {})},
        }
        if req.response_format == "json":
            body["format"] = req.json_schema or "json"
        try:
            r = await self._client.post("/api/chat", json=body, timeout=req.timeout_s)
        except httpx.ConnectError as exc:
            raise ProviderUnavailable(f"Ollama not reachable at {self.base_url}") from exc
        _raise_for(r)
        data = r.json()
        content = data.get("message", {}).get("content", "")
        return CompletionResponse(
            content=content, model=req.model, provider=self.name,
            tokens_in=data.get("prompt_eval_count") or messages_tokens(req.messages),
            tokens_out=data.get("eval_count") or estimate_tokens(content),
            latency_ms=(time.perf_counter() - t0) * 1000, finish_reason=data.get("done_reason"),
            tokens_estimated="eval_count" not in data,
        )

    async def embed(self, texts: list[str], model: str, dim: int) -> list[list[float]]:
        try:
            r = await self._client.post("/api/embed", json={"model": model, "input": texts})
        except httpx.ConnectError as exc:
            raise ProviderUnavailable(f"Ollama not reachable at {self.base_url}") from exc
        _raise_for(r)
        vecs = r.json()["embeddings"]
        if vecs and len(vecs[0]) != dim:
            raise ProviderUnavailable(f"Embedding dim {len(vecs[0])} != configured {dim}")
        return vecs

    async def list_models(self) -> list[str]:
        r = await self._client.get("/api/tags")
        _raise_for(r)
        return [m["name"] for m in r.json().get("models", [])]

    async def health_check(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            models = await self.list_models()
            return {"status": "ok", "models": models[:20], "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "unavailable", "detail": str(exc)}


class OpenAICompatibleProvider(_HttpProvider):
    """OpenAI, NVIDIA NIM, vLLM, LM Studio, Together etc. share the /chat/completions wire format."""

    kind = "openai_compatible"

    def __init__(self, *a: Any, kind: str = "openai_compatible", supports_developer_role: bool = False, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.kind = kind
        self.supports_developer_role = supports_developer_role

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _map(self, m: ChatMessage) -> dict[str, str]:
        role = m.role
        if role == "developer" and not self.supports_developer_role:
            role = "system"
        if role == "tool":
            role = "user"
        return {"role": role, "content": m.content}

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        if self.kind != "openai_compatible":
            self._need_key()
        t0 = time.perf_counter()
        body: dict[str, Any] = {
            "model": req.model, "messages": [self._map(m) for m in req.messages],
            "temperature": req.temperature, "max_tokens": req.max_tokens,
        }
        if req.stop:
            body["stop"] = req.stop
        if req.seed is not None:
            body["seed"] = req.seed
        if req.response_format == "json":
            body["response_format"] = (
                {"type": "json_schema", "json_schema": {"name": "output", "schema": req.json_schema}}
                if req.json_schema else {"type": "json_object"}
            )
        r = await self._client.post("/chat/completions", json=body, headers=self._headers(), timeout=req.timeout_s)
        _raise_for(r)
        data = r.json()
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        content = choice["message"].get("content") or ""
        return CompletionResponse(
            content=content, model=data.get("model", req.model), provider=self.name,
            tokens_in=usage.get("prompt_tokens") or messages_tokens(req.messages),
            tokens_out=usage.get("completion_tokens") or estimate_tokens(content),
            latency_ms=(time.perf_counter() - t0) * 1000, finish_reason=choice.get("finish_reason"),
            tokens_estimated=not usage,
        )

    async def embed(self, texts: list[str], model: str, dim: int) -> list[list[float]]:
        body: dict[str, Any] = {"model": model, "input": texts}
        if model.startswith("text-embedding-3"):
            body["dimensions"] = dim
        r = await self._client.post("/embeddings", json=body, headers=self._headers())
        _raise_for(r)
        return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]

    async def list_models(self) -> list[str]:
        r = await self._client.get("/models", headers=self._headers())
        _raise_for(r)
        return [m["id"] for m in r.json().get("data", [])]

    async def health_check(self) -> dict[str, Any]:
        if self.kind != "openai_compatible" and not self.api_key:
            return {"status": "unconfigured", "detail": "API key not set"}
        t0 = time.perf_counter()
        try:
            models = await self.list_models()
            return {"status": "ok", "models": models[:20], "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "unavailable", "detail": str(exc)[:300]}


class AnthropicProvider(_HttpProvider):
    kind = "anthropic"
    supports_embeddings = False

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        self._need_key()
        t0 = time.perf_counter()
        system = "\n\n".join(m.content for m in req.messages if m.role in ("system", "developer"))
        msgs: list[dict[str, str]] = []
        for m in req.messages:
            if m.role in ("system", "developer"):
                continue
            role = "assistant" if m.role == "assistant" else "user"
            if msgs and msgs[-1]["role"] == role:  # API requires alternation
                msgs[-1]["content"] += "\n\n" + m.content
            else:
                msgs.append({"role": role, "content": m.content})
        if req.response_format == "json":
            system += "\n\nRespond with a single valid JSON object only."
        body: dict[str, Any] = {"model": req.model, "max_tokens": req.max_tokens, "temperature": min(req.temperature, 1.0),
                                "messages": msgs}
        if system:
            body["system"] = system
        if req.stop:
            body["stop_sequences"] = req.stop
        r = await self._client.post(
            "/v1/messages", json=body, timeout=req.timeout_s,
            headers={"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01"},
        )
        _raise_for(r)
        data = r.json()
        content = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage", {})
        return CompletionResponse(
            content=content, model=data.get("model", req.model), provider=self.name,
            tokens_in=usage.get("input_tokens", 0), tokens_out=usage.get("output_tokens", 0),
            latency_ms=(time.perf_counter() - t0) * 1000, finish_reason=data.get("stop_reason"),
        )

    async def embed(self, texts: list[str], model: str, dim: int) -> list[list[float]]:
        raise ProviderUnavailable("Anthropic does not offer an embeddings API; configure another embedding provider")

    async def list_models(self) -> list[str]:
        self._need_key()
        r = await self._client.get("/v1/models", headers={"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01"})
        _raise_for(r)
        return [m["id"] for m in r.json().get("data", [])]

    async def health_check(self) -> dict[str, Any]:
        if not self.api_key:
            return {"status": "unconfigured", "detail": "API key not set"}
        try:
            return {"status": "ok", "models": (await self.list_models())[:20]}
        except Exception as exc:  # noqa: BLE001
            return {"status": "unavailable", "detail": str(exc)[:300]}


class GeminiProvider(_HttpProvider):
    kind = "gemini"

    def _params(self) -> dict[str, str]:
        return {"key": self.api_key or ""}

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        self._need_key()
        t0 = time.perf_counter()
        system = "\n\n".join(m.content for m in req.messages if m.role in ("system", "developer"))
        contents = [{"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
                    for m in req.messages if m.role not in ("system", "developer")]
        gen: dict[str, Any] = {"temperature": req.temperature, "maxOutputTokens": req.max_tokens}
        if req.response_format == "json":
            gen["responseMimeType"] = "application/json"
        body: dict[str, Any] = {"contents": contents, "generationConfig": gen}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        r = await self._client.post(f"/models/{req.model}:generateContent", params=self._params(), json=body, timeout=req.timeout_s)
        _raise_for(r)
        data = r.json()
        cand = (data.get("candidates") or [{}])[0]
        content = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        usage = data.get("usageMetadata", {})
        return CompletionResponse(
            content=content, model=req.model, provider=self.name,
            tokens_in=usage.get("promptTokenCount", messages_tokens(req.messages)),
            tokens_out=usage.get("candidatesTokenCount", estimate_tokens(content)),
            latency_ms=(time.perf_counter() - t0) * 1000, finish_reason=cand.get("finishReason"),
        )

    async def embed(self, texts: list[str], model: str, dim: int) -> list[list[float]]:
        self._need_key()
        reqs = [{"model": f"models/{model}", "content": {"parts": [{"text": t}]}, "outputDimensionality": dim} for t in texts]
        r = await self._client.post(f"/models/{model}:batchEmbedContents", params=self._params(), json={"requests": reqs})
        _raise_for(r)
        return [e["values"] for e in r.json()["embeddings"]]

    async def list_models(self) -> list[str]:
        self._need_key()
        r = await self._client.get("/models", params=self._params())
        _raise_for(r)
        return [m["name"].removeprefix("models/") for m in r.json().get("models", [])]

    async def health_check(self) -> dict[str, Any]:
        if not self.api_key:
            return {"status": "unconfigured", "detail": "API key not set"}
        try:
            return {"status": "ok", "models": (await self.list_models())[:20]}
        except Exception as exc:  # noqa: BLE001
            return {"status": "unavailable", "detail": str(exc)[:300]}
