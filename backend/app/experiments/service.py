"""A/B experimentation: deterministic traffic allocation, variant execution over a dataset, and
statistically-guarded results (CIs, Welch's t-test, minimum sample size)."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailedError
from app.evaluations.engine import DEFAULT_THRESHOLDS, score_case
from app.experiments import stats
from app.models import (
    Agent,
    AgentExecution,
    EvaluationCase,
    Experiment,
    ExperimentObservation,
    ExperimentVariant,
    PromptTemplate,
    PromptVersion,
)
from app.rag import service as rag
from app.security.principal import Principal
from app.services import prompt_service

METRICS = {"quality_score": True, "groundedness": True, "reference_f1": True, "pass": True,
           "latency_ms": False, "cost_usd": False, "tokens": False}


def assign_variant(experiment_id: str, unit_id: str, variants: list[ExperimentVariant]) -> ExperimentVariant:
    """Stable hash bucketing: the same unit always sees the same variant; weights are respected."""
    total = sum(max(0.0, v.traffic_weight) for v in variants)
    if total <= 0:
        raise ValidationFailedError("Variant traffic weights must sum to > 0")
    h = int(hashlib.sha256(f"{experiment_id}:{unit_id}".encode()).hexdigest()[:15], 16) / float(16**15)
    point, acc = h * total, 0.0
    for v in sorted(variants, key=lambda v: v.name):
        acc += max(0.0, v.traffic_weight)
        if point < acc:
            return v
    return variants[-1]


async def _run_variant(db: AsyncSession, exp: Experiment, v: ExperimentVariant, case: EvaluationCase,
                       principal: Principal) -> dict[str, Any]:
    c = v.config or {}
    org = exp.organization_id
    if exp.dimension in ("prompt", "model"):
        tid = c.get("template_id")
        pv = (await db.execute(select(PromptVersion).join(PromptTemplate).where(
            PromptTemplate.id == tid, PromptTemplate.organization_id == org,
            PromptVersion.version == int(c.get("version", 1))))).scalar_one_or_none()
        if pv is None:
            raise ValidationFailedError(f"Variant '{v.name}' references a missing prompt version")
        r = await prompt_service.run_prompt(db, org_id=org, version=pv, variables={"question": case.input, **(case.variables or {})},
                                            context=case.context or None, use_rag=bool(c.get("use_rag", not case.context)),
                                            provider=c.get("provider"), model=c.get("model"))
        return {"output": r["output"], "passages": (case.context or []) + [h["content"] for h in r["retrieved"]],
                "retrieved_files": [h["filename"] for h in r["retrieved"]], "latency_ms": r["latency_ms"],
                "tokens": r["tokens_in"] + r["tokens_out"], "cost": r["cost_usd"], "is_mock": r["is_mock"]}
    if exp.dimension == "retrieval":
        r = await rag.answer(db, org_id=org, question=case.input, provider=c.get("provider", "mock"),
                             model=c.get("model", "mock-reasoner-1"), strategy=c.get("strategy", "hybrid"),
                             top_k=int(c.get("top_k", 4)), compress_context=bool(c.get("compress", False)))
        hits = r["retrieval"]["hits"]
        return {"output": r["answer"], "passages": [h["content"] for h in hits], "retrieved_files": [h["filename"] for h in hits],
                "latency_ms": r["latency_ms"] + r["retrieval"]["latency_ms"], "tokens": r["tokens_in"] + r["tokens_out"],
                "cost": r["cost_usd"], "is_mock": r["is_mock"]}
    if exp.dimension == "agent":
        from app.agents import runtime

        agent = (await db.execute(select(Agent).where(Agent.id == c.get("agent_id"), Agent.organization_id == org))).scalar_one()
        ex = await runtime.create_execution(db, org_id=org, agent=agent, principal=principal, input_text=case.input,
                                            version=c.get("version"))
        await db.commit()
        await runtime._run(ex.id, principal)
        row = (await db.execute(select(AgentExecution).where(AgentExecution.id == ex.id))).scalar_one()
        await db.refresh(row)
        ev = row.evaluation or {}
        return {"output": row.output or "", "passages": ev.get("passages") or [],
                "retrieved_files": [x.get("filename") for x in row.citations or []], "latency_ms": row.latency_ms,
                "tokens": row.tokens_in + row.tokens_out, "cost": row.cost_usd, "is_mock": row.is_mock}
    raise ValidationFailedError(f"Unknown dimension {exp.dimension}")


async def run(db: AsyncSession, exp: Experiment, principal: Principal, mode: str = "paired", repeats: int = 1) -> dict:
    variants = (await db.execute(select(ExperimentVariant).where(ExperimentVariant.experiment_id == exp.id))).scalars().all()
    if len(variants) < 2:
        raise ValidationFailedError("An experiment needs at least two variants")
    if not exp.dataset_id:
        raise ValidationFailedError("Attach an evaluation dataset to run the experiment")
    cases = (await db.execute(select(EvaluationCase).where(EvaluationCase.dataset_id == exp.dataset_id))).scalars().all()
    exp.status = "running"
    created = 0
    for rep in range(max(1, min(repeats, 10))):
        for case in cases:
            unit = f"{case.id}:{rep}"
            targets = variants if mode == "paired" else [assign_variant(exp.id, unit, list(variants))]
            for v in targets:
                gen = await _run_variant(db, exp, v, case, principal)
                scores, passed, _ = score_case(case, gen, DEFAULT_THRESHOLDS)
                quality = [x for x in (scores.get("reference_f1"), scores.get("groundedness"),
                                       scores.get("citation_precision"), scores.get("completeness")) if x is not None]
                db.add(ExperimentObservation(experiment_id=exp.id, variant_id=v.id, unit_id=unit, metrics={
                    "quality_score": sum(quality) / len(quality) if quality else None,
                    "groundedness": scores.get("groundedness"), "reference_f1": scores.get("reference_f1"),
                    "pass": 1.0 if passed else 0.0, "latency_ms": gen.get("latency_ms"), "cost_usd": gen.get("cost"),
                    "tokens": gen.get("tokens"), "is_mock": gen.get("is_mock", False)}))
                created += 1
    exp.status = "completed"
    await db.flush()
    return {"observations_created": created, "mode": mode}


async def results(db: AsyncSession, exp: Experiment) -> dict[str, Any]:
    variants = (await db.execute(select(ExperimentVariant).where(ExperimentVariant.experiment_id == exp.id))).scalars().all()
    obs = (await db.execute(select(ExperimentObservation).where(ExperimentObservation.experiment_id == exp.id))).scalars().all()
    by_v: dict[str, list[dict]] = {v.id: [] for v in variants}
    for o in obs:
        by_v.setdefault(o.variant_id, []).append(o.metrics)
    control = next((v for v in variants if v.is_control), variants[0] if variants else None)
    out_variants = []
    for v in variants:
        rows = by_v.get(v.id, [])
        m = {}
        for k in METRICS:
            xs = [float(r[k]) for r in rows if r.get(k) is not None]
            m[k] = stats.wilson(int(sum(xs)), len(xs)) if k == "pass" else stats.mean_ci(xs)
        out_variants.append({"id": v.id, "name": v.name, "is_control": v.is_control, "traffic_weight": v.traffic_weight,
                             "n": len(rows), "metrics": m, "config": v.config})
    comparisons = []
    if control:
        cvals = by_v.get(control.id, [])
        for v in variants:
            if v.id == control.id:
                continue
            tvals = by_v.get(v.id, [])
            comp = {"variant": v.name, "control": control.name, "metrics": {}}
            for k, hib in METRICS.items():
                a = [float(r[k]) for r in cvals if r.get(k) is not None]
                b = [float(r[k]) for r in tvals if r.get(k) is not None]
                d = stats.welch(a, b)
                rel = (d["diff"] / abs(sum(a) / len(a))) if a and d["diff"] is not None and sum(a) else None
                comp["metrics"][k] = {**d, "relative_change": rel,
                                      **stats.verdict(d, len(a), len(b), exp.min_sample_size, hib)}
            comparisons.append(comp)
    primary = exp.primary_metric
    return {"experiment_id": exp.id, "primary_metric": primary, "min_sample_size": exp.min_sample_size,
            "total_observations": len(obs), "variants": out_variants, "comparisons": comparisons,
            "is_mock": any(o.metrics.get("is_mock") for o in obs),
            "guardrail": "Significance is only claimed when every arm reaches min_sample_size and the 95% CI excludes 0."}
