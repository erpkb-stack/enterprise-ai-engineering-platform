"""Provider-agnostic LLM interface. Business services depend only on these types."""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "developer", "user", "assistant", "tool"]


class ChatMessage(BaseModel):
    role: Role
    content: str
    name: str | None = None


class CompletionRequest(BaseModel):
    messages: list[ChatMessage]
    model: str
    temperature: float = Field(0.2, ge=0, le=2)
    max_tokens: int = Field(1024, ge=1, le=32_000)
    timeout_s: float = 60.0
    response_format: Literal["text", "json"] = "text"
    json_schema: dict[str, Any] | None = None
    stop: list[str] | None = None
    seed: int | None = None


class CompletionResponse(BaseModel):
    content: str
    model: str
    provider: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    is_mock: bool = False
    finish_reason: str | None = None
    tokens_estimated: bool = False


class ProviderUnavailable(Exception):
    """Raised when a capability is not supported or not configured (e.g. missing API key)."""


class LLMProvider(ABC):
    kind: str = "abstract"
    is_mock: bool = False
    supports_embeddings: bool = True

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...

    @abstractmethod
    async def embed(self, texts: list[str], model: str, dim: int) -> list[list[float]]: ...

    @abstractmethod
    async def list_models(self) -> list[str]: ...

    @abstractmethod
    async def health_check(self) -> dict[str, Any]: ...

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        pass


_WORD = re.compile(r"\w+|[^\w\s]")


def estimate_tokens(text: str) -> int:
    """Heuristic BPE-like token estimate (~±10% of cl100k for English prose). Labelled as an estimate in the UI."""
    if not text:
        return 0
    pieces = _WORD.findall(text)
    long_words = sum(max(0, math.ceil(len(p) / 6) - 1) for p in pieces if len(p) > 6)
    return max(1, round(0.5 * (len(pieces) + long_words) + 0.5 * (len(text) / 4)))


def messages_tokens(messages: list[ChatMessage]) -> int:
    return sum(estimate_tokens(m.content) + 4 for m in messages) + 2


def estimate_cost(tokens_in: int, tokens_out: int, in_per_1k: float, out_per_1k: float) -> float:
    return round(tokens_in / 1000 * in_per_1k + tokens_out / 1000 * out_per_1k, 6)


# Public list-price defaults (USD / 1K tokens) used when a model configuration doesn't override them.
# Documented as estimates; verify against provider pricing pages.
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "claude-sonnet-5-5": (0.003, 0.015),
    "claude-haiku-5-5": (0.001, 0.005),
    "gemini-2.0-flash": (0.0001, 0.0004),
    "meta/llama-3.1-8b-instruct": (0.0003, 0.0003),
}


def pricing_for(model: str) -> tuple[float, float]:
    return DEFAULT_PRICING.get(model, (0.0, 0.0))
