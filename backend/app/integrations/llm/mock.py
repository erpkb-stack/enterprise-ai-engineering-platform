"""Deterministic mock provider for credential-free local development and reproducible tests.

This is NOT a language model. It is a transparent rule-based stand-in:
  * agent protocol: picks the tool whose name/description best overlaps the request, then answers
    extractively from tool observations, citing passage numbers it actually used;
  * grounded QA: selects the most query-relevant sentences from the supplied context with [n] citations;
  * judge prompts: returns a lexical-overlap score with an explicit "mock judge" rationale;
  * embeddings: signed feature hashing of word unigrams + character trigrams (L2-normalised).

Every response is flagged `is_mock=True` and the UI renders a MOCK badge.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from typing import Any

from app.integrations.llm.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    estimate_tokens,
    messages_tokens,
)

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = set(
    "a an the of to in on for and or is are was were be been it this that with as by at from what which who how why "
    "when where do does did can could should would will i you we they he she me my our your their its about into "
    "please tell explain describe give list show summarize".split()
)
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
_PASSAGE = re.compile(r"\[(\d+)\]\s*(?:\(([^)]*)\))?\s*(.+?)(?=\n\[\d+\]|\Z)", re.S)


def tokens(text: str) -> list[str]:
    # crude prefix stemming (incident/incidents, acknowledged/acknowledgement) keeps the mock deterministic
    return [t[:6] for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


_FENCE = re.compile(r"<untrusted_tool_output>\s*(.*?)\s*</untrusted_tool_output>", re.S)


def _fenced(obs: str) -> str:
    m = _FENCE.search(obs)
    return m.group(1) if m else obs.split(":", 1)[-1]


def _summarize_tool(obs: str) -> str:
    """Render a tool's JSON result as a compact fact list (never echoes instructions)."""
    name = re.search(r"OBSERVATION from (\S+):", obs)
    body = _fenced(obs)
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return f"{name.group(1) if name else 'tool'} returned: {body[:200]}"
    if isinstance(data, dict):
        facts = ", ".join(f"{k}={v}" for k, v in data.items() if not isinstance(v, dict | list) and k != "demo_data")
    else:
        facts = json.dumps(data)[:300]
    return f"{name.group(1) if name else 'tool'} reported {facts}"


def overlap(q: list[str], d: list[str]) -> float:
    if not q or not d:
        return 0.0
    qs, ds = set(q), set(d)
    return len(qs & ds) / math.sqrt(len(qs) * len(ds))


def hash_embed(text: str, dim: int = 768) -> list[float]:
    vec = [0.0] * dim
    low = text.lower()
    feats = tokens(low)
    feats += [low[i : i + 3] for i in range(max(0, len(low) - 2)) if low[i : i + 3].strip()]
    for f in feats:
        h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
        vec[h % dim] += 1.0 if (h >> 63) & 1 else -1.0
    if not any(vec):
        vec[0] = 1.0  # never emit a zero vector (cosine distance would be NaN)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class MockProvider(LLMProvider):
    kind = "mock"
    is_mock = True

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        t0 = time.perf_counter()
        system = "\n".join(m.content for m in req.messages if m.role in ("system", "developer"))
        users = [m for m in req.messages if m.role == "user"]
        last_user = users[-1].content if users else ""
        if "AIECC_AGENT_PROTOCOL" in system:
            content = self._agent_step(system, req.messages)
        elif "AIECC_JUDGE" in system:
            content = self._judge(last_user)
        elif req.response_format == "json":
            content = self._json(last_user, req.json_schema)
        elif "AIECC_CODE_REVIEW" in system:
            content = self._code_review(last_user)
        else:
            content = self._grounded_answer(last_user, system + "\n" + last_user)
        out_tokens = estimate_tokens(content)
        return CompletionResponse(
            content=content,
            model=req.model,
            provider=self.name,
            tokens_in=messages_tokens(req.messages),
            tokens_out=out_tokens,
            latency_ms=(time.perf_counter() - t0) * 1000,
            is_mock=True,
            finish_reason="stop",
            tokens_estimated=True,
        )

    # --- behaviours -------------------------------------------------------------------------
    @staticmethod
    def _passages(text: str) -> list[tuple[int, str]]:
        return [(int(m.group(1)), m.group(3).strip()) for m in _PASSAGE.finditer(text)]

    def _extract(self, question: str, passages: list[tuple[int, str]], k: int = 3) -> str:
        q = tokens(question)
        scored: list[tuple[float, int, str]] = []
        for n, body in passages:
            for s in _SENT.split(body):
                s = s.strip()
                if len(s) < 20 or s.startswith("#") or "(DEMO DATA)" in s:
                    continue
                scored.append((overlap(q, tokens(s)), n, s))
        scored.sort(key=lambda x: -x[0])
        picked = [x for x in scored if x[0] > 0][:k]
        if not picked:
            return "I could not find information in the provided sources to answer this question."
        return " ".join(f"{s.rstrip('.')} [{n}]." for _, n, s in picked)

    def _grounded_answer(self, question: str, full: str) -> str:
        passages = self._passages(full)
        if passages:
            return self._extract(question, passages)
        return (
            "[mock provider] No context passages were supplied, so no grounded answer can be produced. "
            "Configure Ollama or a hosted provider for open-ended generation."
        )

    def _agent_step(self, system: str, messages: list[ChatMessage]) -> str:
        observations = [m.content for m in messages if m.role == "tool" or m.content.startswith("OBSERVATION")]
        question = next((m.content for m in messages if m.role == "user"), "")
        tools = re.findall(r"^- (\S+): (.*)$", system, re.M)
        used = set(re.findall(r'"tool":\s*"([^"]+)"', "\n".join(m.content for m in messages if m.role == "assistant")))
        q = tokens(question)
        candidates = [(overlap(q, tokens(n.replace("_", " ") + " " + d)), n) for n, d in tools if n not in used]
        # Prefer retrieval first for research-style questions, then the best-overlapping other tool.
        if not observations and any(n == "knowledge_search" for _, n in candidates):
            return json.dumps({"thought": "Retrieve grounding documents first.", "action": "tool",
                               "tool": "knowledge_search", "arguments": {"query": question, "top_k": 4}})
        candidates.sort(reverse=True)
        if len(observations) < 2 and candidates and candidates[0][0] >= 0.15:
            name = candidates[0][1]
            schema_args = self._args_for(name, system, question)
            return json.dumps({"thought": f"Tool '{name}' matches the request.", "action": "tool",
                               "tool": name, "arguments": schema_args})
        kb = [_fenced(o) for o in observations if "#chunk" in o]
        tool_obs = [o for o in observations if "#chunk" not in o and "<untrusted_tool_output>" in o]
        answer = self._extract(question, self._passages("\n".join(kb)), k=2) if kb else ""
        facts = " ".join(_summarize_tool(o) + "." for o in tool_obs)
        if facts and (not answer or "could not find" in answer):
            answer = facts
        elif facts:
            answer = f"{answer} {facts}"
        answer = answer or "I could not find information to answer this question."
        return json.dumps({"thought": "I have enough information.", "action": "final", "answer": answer})

    @staticmethod
    def _args_for(tool: str, system: str, question: str) -> dict[str, Any]:
        m = re.search(rf"^- {re.escape(tool)}: .*?args=(\{{.*\}})$", system, re.M)
        props: dict[str, Any] = {}
        required: set[str] = set()
        if m:
            try:
                schema = json.loads(m.group(1))
                props, required = schema.get("properties", {}), set(schema.get("required", []))
            except json.JSONDecodeError:
                props = {}
        args: dict[str, Any] = {}
        nums = re.findall(r"-?\d+(?:\.\d+)?", question)
        words = re.findall(r"[A-Za-z][\w-]+", question)
        for i, (k, spec) in enumerate(props.items()):
            t = spec.get("type")
            if t in ("number", "integer"):
                args[k] = (float(nums[i]) if t == "number" else int(float(nums[i]))) if i < len(nums) else 1
            elif "enum" in spec:
                args[k] = next((e for e in spec["enum"] if e.lower() in question.lower()), spec["enum"][0])
            elif k in ("expression",):
                expr = re.findall(r"[\d\.\s\+\-\*/\(\)]{3,}", question)
                args[k] = expr[0].strip() if expr else "1+1"
            else:
                cand = [w for w in words if w.lower() not in _STOP]
                desc = (m.group(0) if m else "").lower()
                enum_like = " ".join(re.findall(r"\(([^)]*)\)", desc))  # e.g. "(checkout, search, auth)"
                hinted = [w for w in cand if re.search(rf"\b{re.escape(w.lower())}\b", enum_like)]
                hinted = hinted or [w for w in cand if w.lower() in desc]  # prefer values the tool documents
                if k in ("service", "name", "id"):
                    args[k] = (hinted or cand or [question])[0].lower()
                elif k not in required and "default" in spec:
                    args[k] = spec["default"]
                elif k in required:
                    args[k] = question
        return args

    def _judge(self, user: str) -> str:
        ans = re.search(r"ANSWER:\s*(.*?)(?:\nREFERENCE:|\nCONTEXT:|\Z)", user, re.S)
        ref = re.search(r"(?:REFERENCE|CONTEXT):\s*(.*)", user, re.S)
        a, r = (ans.group(1) if ans else user), (ref.group(1) if ref else "")
        score = round(min(1.0, overlap(tokens(a), tokens(r)) * 1.4), 3)
        return json.dumps({"score": score, "reasoning": "mock judge: lexical overlap proxy, not semantic judgement",
                           "is_mock_judge": True})

    def _json(self, user: str, schema: dict | None) -> str:
        props = (schema or {}).get("properties", {})
        out: dict[str, Any] = {}
        for k, spec in props.items():
            t = spec.get("type", "string")
            out[k] = {"string": user[:80], "number": 0.0, "integer": 0, "boolean": False, "array": [], "object": {}}.get(t, None)
        return json.dumps(out or {"result": user[:200]})

    def _code_review(self, code: str) -> str:
        funcs = re.findall(r"(?:async\s+)?def\s+(\w+)\(", code)
        return json.dumps({
            "summary": f"[mock] Structural review of {len(funcs)} function(s). Configure a real model for semantic review.",
            "suggestions": [{"title": f"Add tests for `{f}`", "detail": "Cover happy path, edge cases and failure modes.",
                             "confidence": "low"} for f in funcs[:5]],
            "generated_tests": "\n\n".join(
                f"def test_{f}_smoke():\n    # TODO(mock): arrange inputs and assert on {f}() behaviour\n    ..." for f in funcs[:5]
            ),
        })

    async def embed(self, texts: list[str], model: str, dim: int) -> list[list[float]]:
        return [hash_embed(t, dim) for t in texts]

    async def list_models(self) -> list[str]:
        return ["mock-reasoner-1", "hash-embed-768"]

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "detail": "deterministic mock provider (no network)", "is_mock": True}
