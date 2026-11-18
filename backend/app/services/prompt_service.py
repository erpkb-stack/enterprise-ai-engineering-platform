"""Prompt rendering (sandboxed Jinja2), token estimation and prompt test execution."""

from __future__ import annotations

import asyncio
from typing import Any

from jinja2 import StrictUndefined, TemplateSyntaxError, UndefinedError, meta
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailedError
from app.evaluations import grounding, scorers
from app.integrations.llm.base import ChatMessage, CompletionRequest, estimate_tokens
from app.integrations.llm.gateway import Route, gateway
from app.models import PromptVersion
from app.rag import service as rag

_env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)


def declared_variables(*templates: str) -> list[str]:
    names: set[str] = set()
    for t in templates:
        try:
            names |= meta.find_undeclared_variables(_env.parse(t or ""))
        except TemplateSyntaxError as exc:
            raise ValidationFailedError(f"Template syntax error: {exc.message} (line {exc.lineno})") from exc
    return sorted(names)


def render(template: str, variables: dict[str, Any]) -> str:
    try:
        return _env.from_string(template or "").render(**variables)
    except UndefinedError as exc:
        raise ValidationFailedError(f"Missing template variable: {exc.message}") from exc
    except TemplateSyntaxError as exc:
        raise ValidationFailedError(f"Template syntax error: {exc.message}") from exc


def build_messages(v: PromptVersion, variables: dict[str, Any], sources: str | None = None) -> list[ChatMessage]:
    msgs: list[ChatMessage] = []
    if v.system:
        msgs.append(ChatMessage(role="system", content=render(v.system, variables)))
    if v.developer:
        msgs.append(ChatMessage(role="developer", content=render(v.developer, variables)))
    user = render(v.user_template, {**variables, "sources": sources or variables.get("sources", "")})
    if sources and "{{ sources" not in (v.user_template or "") and "{{sources" not in (v.user_template or ""):
        user = f"SOURCES:\n{sources}\n\n{user}"
    msgs.append(ChatMessage(role="user", content=user))
    return msgs


def preview(v: PromptVersion, variables: dict[str, Any]) -> dict[str, Any]:
    msgs = build_messages(v, variables)
    return {"messages": [m.model_dump() for m in msgs],
            "token_estimate": {"total": sum(estimate_tokens(m.content) + 4 for m in msgs) + 2,
                               "per_message": [estimate_tokens(m.content) for m in msgs],
                               "method": "heuristic estimate (±10%)"},
            "variables": declared_variables(v.system, v.developer, v.user_template)}


async def run_prompt(db: AsyncSession, *, org_id: str, version: PromptVersion, variables: dict[str, Any],
                     context: list[str] | None = None, use_rag: bool = False, rag_query: str | None = None,
                     collection: str | None = None, provider: str | None = None, model: str | None = None,
                     repeats: int = 1, must_contain: list[str] | None = None, must_not_contain: list[str] | None = None,
                     reference: str | None = None) -> dict[str, Any]:
    mc = version.model_config_json or {}
    provider = provider or mc.get("provider", "mock")
    model = model or mc.get("model", "mock-reasoner-1")
    passages: list[str] = list(context or [])
    hits: list[dict] = []
    if use_rag:
        q = rag_query or variables.get("question") or variables.get("query") or next(iter(variables.values()), "")
        res = await rag.search(db, org_id=org_id, query=str(q), strategy="hybrid", top_k=4, collection=collection)
        hits = res["hits"]
        passages += [h["content"] for h in hits]
    sources = "\n".join(f"[{i}] {p}" for i, p in enumerate(passages, 1)) if passages else None
    msgs = build_messages(version, variables, sources)
    req = CompletionRequest(messages=msgs, model=model, temperature=float(mc.get("temperature", 0.2)),
                            max_tokens=int(mc.get("max_tokens", 700)),
                            response_format="json" if version.output_schema else "text", json_schema=version.output_schema)
    repeats = max(1, min(repeats, 5))
    results = await asyncio.gather(*[
        gateway.complete(req, route=Route(provider, model), db=db, org_id=org_id, purpose="prompt_test")
        for _ in range(repeats)
    ])
    outputs = [r[0].content for r in results]
    first, meta0 = results[0]
    ground = grounding.verify(first.content, passages).as_dict() if passages else None
    valid, why = scorers.structured_validity(first.content, version.output_schema)
    version.execution_count += repeats
    return {
        "output": first.content, "outputs": outputs if repeats > 1 else None,
        "provider": meta0["provider"], "model": meta0["model"], "is_mock": first.is_mock,
        "latency_ms": round(sum(r[0].latency_ms for r in results) / repeats, 2),
        "tokens_in": first.tokens_in, "tokens_out": first.tokens_out, "cost_usd": sum(r[1]["cost_usd"] for r in results),
        "messages": [m.model_dump() for m in msgs], "retrieved": hits,
        "checks": {
            "grounding": ground,
            "structured_valid": valid, "structured_error": why,
            "instruction_adherence": scorers.instruction_adherence(first.content, must_contain or [], must_not_contain or []),
            "consistency": scorers.consistency(outputs),
            "context_relevance": (sum(grounding.support_score(str(variables.get("question", "")), p) for p in passages)
                                  / len(passages)) if passages and variables.get("question") else None,
            "reference_f1": scorers.token_f1(first.content, reference),
            "leakage": scorers.leakage(first.content),
        },
    }
