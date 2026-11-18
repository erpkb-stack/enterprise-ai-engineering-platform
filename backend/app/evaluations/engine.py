"""Evaluation engine: runs a dataset against a target (prompt version, agent, or RAG pipeline),
scores every case with deterministic scorers (+ optional, clearly-labelled LLM judge), persists
results, and computes a summary with regression analysis against a baseline run."""

from __future__ import annotations

import asyncio
import json
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.ids import utcnow
from app.core.logging import get_logger
from app.evaluations import grounding, scorers
from app.integrations.llm.base import ChatMessage, CompletionRequest
from app.integrations.llm.gateway import Route, gateway
from app.models import (
    Agent,
    AgentExecution,
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
    PromptTemplate,
    PromptVersion,
)
from app.observability.events import bus
from app.observability.metrics import percentile
from app.rag import service as rag
from app.security.principal import Principal
from app.services import prompt_service

log = get_logger(__name__)

JUDGE_SYSTEM = (
    "AIECC_JUDGE. You are an evaluation judge. Score how faithfully the ANSWER is supported by the REFERENCE/CONTEXT "
    'on a 0-1 scale. Reply with JSON {"score": <0-1>, "reasoning": "<one sentence>"}.'
)
JUDGE_LIMITATIONS = ("LLM-as-judge scores are model opinions: they can be biased toward verbose or self-similar answers, "
                     "are sensitive to prompt wording, and are not reproducible across model versions. They are reported "
                     "separately and never used alone for pass/fail.")

DEFAULT_THRESHOLDS = {"reference_f1": 0.3, "groundedness": 0.6, "citation_precision": 0.5, "instruction_adherence": 1.0}


async def _generate(db: AsyncSession, run: EvaluationRun, case: EvaluationCase, principal: Principal) -> dict[str, Any]:
    cfg = run.config or {}
    if run.target_type == "prompt":
        v = (await db.execute(select(PromptVersion).join(PromptTemplate).where(
            PromptTemplate.id == run.target_id, PromptTemplate.organization_id == run.organization_id,
            PromptVersion.version == run.target_version))).scalar_one()
        variables = {"question": case.input, "input": case.input, **(case.variables or {})}
        r = await prompt_service.run_prompt(db, org_id=run.organization_id, version=v, variables=variables,
                                            context=case.context or None, use_rag=bool(cfg.get("use_rag")) and not case.context,
                                            provider=cfg.get("provider"), model=cfg.get("model"))
        passages = (case.context or []) + [h["content"] for h in r["retrieved"]]
        files = [h["filename"] for h in r["retrieved"]]
        return {"output": r["output"], "passages": passages, "retrieved_files": files, "latency_ms": r["latency_ms"],
                "tokens": r["tokens_in"] + r["tokens_out"], "cost": r["cost_usd"], "is_mock": r["is_mock"],
                "schema": v.output_schema}
    if run.target_type == "rag":
        r = await rag.answer(db, org_id=run.organization_id, question=case.input,
                             provider=cfg.get("provider", "mock"), model=cfg.get("model", "mock-reasoner-1"),
                             strategy=cfg.get("strategy", "hybrid"), top_k=int(cfg.get("top_k", 4)),
                             collection=cfg.get("collection"))
        hits = r["retrieval"]["hits"]
        return {"output": r["answer"], "passages": [h["content"] for h in hits],
                "retrieved_files": [h["filename"] for h in hits], "latency_ms": r["latency_ms"],
                "tokens": r["tokens_in"] + r["tokens_out"], "cost": r["cost_usd"], "is_mock": r["is_mock"]}
    if run.target_type == "agent":
        from app.agents import runtime

        agent = (await db.execute(select(Agent).where(Agent.id == run.target_id,
                                                      Agent.organization_id == run.organization_id))).scalar_one()
        ex = await runtime.create_execution(db, org_id=run.organization_id, agent=agent, principal=principal,
                                            input_text=case.input, version=run.target_version)
        await db.commit()
        await runtime._run(ex.id, principal)
        row = (await db.execute(select(AgentExecution).where(AgentExecution.id == ex.id))).scalar_one()
        await db.refresh(row)
        ev = row.evaluation or {}
        passages = list(ev.get("passages") or [])
        files = [c.get("filename", "") for c in (row.citations or [])]
        return {"output": row.output or row.error or "", "passages": passages, "retrieved_files": files,
                "latency_ms": row.latency_ms, "tokens": row.tokens_in + row.tokens_out, "cost": row.cost_usd,
                "is_mock": row.is_mock, "status": row.status}
    raise ValueError(f"unknown target_type {run.target_type}")


async def _judge(db: AsyncSession, run: EvaluationRun, output: str, reference: str) -> dict[str, Any] | None:
    cfg = run.config or {}
    if not cfg.get("llm_judge") or not get_settings().flag("llm_judge"):
        return None
    judge = cfg.get("judge") or {"provider": cfg.get("provider", "mock"), "model": cfg.get("model", "mock-reasoner-1")}
    req = CompletionRequest(model=judge["model"], temperature=0, max_tokens=200, response_format="json", messages=[
        ChatMessage(role="system", content=JUDGE_SYSTEM),
        ChatMessage(role="user", content=f"ANSWER: {output}\nREFERENCE: {reference}"),
    ])
    try:
        resp, _ = await gateway.complete(req, route=Route(judge["provider"], judge["model"]), db=db,
                                         org_id=run.organization_id, purpose="llm_judge")
        obj = json.loads(resp.content[resp.content.find("{"): resp.content.rfind("}") + 1])
        return {"score": max(0.0, min(1.0, float(obj.get("score", 0)))), "reasoning": str(obj.get("reasoning", ""))[:300],
                "judge": judge, "is_mock": resp.is_mock}
    except Exception as exc:  # noqa: BLE001
        return {"score": None, "error": str(exc)[:200], "judge": judge}


def score_case(case: EvaluationCase, gen: dict[str, Any], thresholds: dict[str, float]) -> tuple[dict, bool, dict]:
    out = gen["output"]
    passages = gen.get("passages") or []
    ground = grounding.verify(out, passages) if passages else None
    cited_files = []
    if ground:
        files = gen.get("retrieved_files") or []
        for c in ground.claims:
            for n in c.valid_citations:
                if n - 1 < len(files):
                    cited_files.append(files[n - 1])
    struct, struct_err = scorers.structured_validity(out, gen.get("schema"))
    leak = scorers.leakage(out)
    s = {
        "exact_match": scorers.exact_match(out, case.reference_answer),
        "reference_f1": scorers.token_f1(out, case.reference_answer),
        "completeness": scorers.completeness(out, case.reference_answer),
        "groundedness": ground.groundedness if ground else None,
        "hallucination_rate": ground.hallucination_rate if ground else None,
        "citation_precision": ground.citation_precision if ground else None,
        "citation_correctness": scorers.citation_correctness(cited_files, case.expected_citations or []),
        "retrieval_relevance": scorers.retrieval_relevance(gen.get("retrieved_files") or [], case.expected_citations or []),
        "instruction_adherence": scorers.instruction_adherence(out, case.must_contain or [], case.must_not_contain or []),
        "structured_validity": struct,
        "toxicity": scorers.toxicity(out),
        "data_leakage": 1.0 if leak["leaked"] else 0.0,
        "latency_ms": gen.get("latency_ms"),
        "tokens": gen.get("tokens"),
        "cost_usd": gen.get("cost"),
    }
    failures = []
    for k, th in thresholds.items():
        v = s.get(k)
        if v is not None and v < th:
            failures.append(f"{k} {v:.2f} < {th}")
    if s["toxicity"] > 0:
        failures.append("toxic content")
    if leak["leaked"]:
        failures.append(f"data leakage: {sorted(leak['types'])}")
    if struct == 0.0:
        failures.append(f"structured output invalid: {struct_err}")
    details = {"failures": failures, "grounding": ground.as_dict() if ground else None, "leakage": leak,
               "is_mock": gen.get("is_mock", False)}
    return s, not failures, details


def summarize(results: list[EvaluationResult]) -> dict[str, Any]:
    keys = sorted({k for r in results for k, v in (r.scores or {}).items() if isinstance(v, int | float)})
    agg = {}
    for k in keys:
        vals = [r.scores[k] for r in results if isinstance(r.scores.get(k), int | float)]
        if vals:
            agg[k] = {"mean": round(mean(vals), 4), "n": len(vals)}
    lat = sorted(r.latency_ms for r in results)
    judge = [r.details.get("llm_judge", {}).get("score") for r in results if (r.details or {}).get("llm_judge")]
    judge = [j for j in judge if j is not None]
    return {
        "cases": len(results), "passed": sum(r.passed for r in results),
        "pass_rate": round(sum(r.passed for r in results) / len(results), 4) if results else None,
        "metrics": agg,
        "latency": {"p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95), "max_ms": lat[-1] if lat else None},
        "total_tokens": sum(r.tokens for r in results), "total_cost_usd": round(sum(r.cost_usd for r in results), 6),
        "llm_judge": {"mean": round(mean(judge), 4), "n": len(judge), "limitations": JUDGE_LIMITATIONS} if judge else None,
        "is_mock": any((r.details or {}).get("is_mock") for r in results),
    }


async def compare(db: AsyncSession, run: EvaluationRun, baseline: EvaluationRun) -> dict[str, Any]:
    cur = {r.case_id: r for r in (await db.execute(select(EvaluationResult).where(EvaluationResult.run_id == run.id))).scalars()}
    base = {r.case_id: r for r in (await db.execute(select(EvaluationResult).where(EvaluationResult.run_id == baseline.id))).scalars()}
    regressions = [cid for cid, r in cur.items() if cid in base and base[cid].passed and not r.passed]
    fixes = [cid for cid, r in cur.items() if cid in base and not base[cid].passed and r.passed]
    deltas = {}
    for k, v in (run.summary.get("metrics") or {}).items():
        b = (baseline.summary.get("metrics") or {}).get(k)
        if b:
            deltas[k] = round(v["mean"] - b["mean"], 4)
    return {"baseline_run_id": baseline.id, "regressions": regressions, "fixes": fixes, "metric_deltas": deltas,
            "pass_rate_delta": (run.summary.get("pass_rate") or 0) - (baseline.summary.get("pass_rate") or 0)}


async def execute_run(run_id: str, principal: Principal) -> None:
    async with get_sessionmaker()() as db:
        run = (await db.execute(select(EvaluationRun).where(EvaluationRun.id == run_id))).scalar_one()
        run.status = "running"
        await db.commit()
        thresholds = {**DEFAULT_THRESHOLDS, **((run.config or {}).get("thresholds") or {})}
        cases = (await db.execute(select(EvaluationCase).where(EvaluationCase.dataset_id == run.dataset_id)
                                  .order_by(EvaluationCase.created_at))).scalars().all()
        results: list[EvaluationResult] = []
        try:
            for i, case in enumerate(cases):
                try:
                    gen = await asyncio.wait_for(_generate(db, run, case, principal), timeout=180)
                except Exception as exc:  # noqa: BLE001
                    gen = {"output": f"<generation error: {exc}>", "passages": [], "latency_ms": 0, "tokens": 0, "cost": 0}
                scores, passed, details = score_case(case, gen, thresholds)
                if case.reference_answer:
                    j = await _judge(db, run, gen["output"], case.reference_answer)
                    if j:
                        details["llm_judge"] = j
                        scores["llm_judge"] = j.get("score")
                res = EvaluationResult(run_id=run.id, case_id=case.id, output=gen["output"][:20_000], scores=scores,
                                       passed=passed, latency_ms=float(gen.get("latency_ms") or 0),
                                       tokens=int(gen.get("tokens") or 0), cost_usd=float(gen.get("cost") or 0),
                                       details=details)
                db.add(res)
                results.append(res)
                await db.commit()
                bus.publish(run.organization_id, "evaluation.progress", {"run_id": run.id, "done": i + 1, "total": len(cases)})
            run.summary = summarize(results)
            if run.baseline_run_id:
                base = (await db.execute(select(EvaluationRun).where(EvaluationRun.id == run.baseline_run_id))).scalar_one_or_none()
                if base and base.summary:
                    run.summary = {**run.summary, "comparison": await compare(db, run, base)}
            run.status = "completed"
        except Exception as exc:  # noqa: BLE001
            log.exception("evaluation_failed")
            run.status, run.error = "failed", str(exc)[:1000]
        run.finished_at = utcnow()
        from app.security import audit

        await audit.record(db, org_id=run.organization_id, action="evaluation.run", category="evaluation",
                           actor_id=principal.user_id, actor_email=principal.email, resource_type="evaluation_run",
                           resource_id=run.id, outcome="success" if run.status == "completed" else "failure",
                           details={"cases": len(cases), "pass_rate": (run.summary or {}).get("pass_rate"),
                                    "target_type": run.target_type})
        await db.commit()
        bus.publish(run.organization_id, "evaluation.completed", {"run_id": run.id, "status": run.status})
