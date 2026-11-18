from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import Pagination, get_owned, paginate, to_dict
from app.core import jobs
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ConflictError, FeatureDisabledError, NotFoundError
from app.core.ids import new_id
from app.evaluations.engine import DEFAULT_THRESHOLDS, JUDGE_LIMITATIONS
from app.evaluations.redteam import SCENARIOS
from app.models import EvaluationCase, EvaluationDataset, EvaluationResult, EvaluationRun
from app.security import audit
from app.security.principal import Principal, require

router = APIRouter(tags=["evaluations"])


class CaseIn(BaseModel):
    input: str = Field(min_length=1, max_length=20_000)
    variables: dict[str, Any] = Field(default_factory=dict)
    context: list[str] = Field(default_factory=list)
    reference_answer: str | None = None
    expected_citations: list[str] = Field(default_factory=list)
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class DatasetIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str = ""
    kind: Literal["golden", "redteam"] = "golden"
    cases: list[CaseIn] = Field(default_factory=list)


class RunIn(BaseModel):
    dataset_id: str
    target_type: Literal["prompt", "agent", "rag"]
    target_id: str | None = None
    target_version: int | None = None
    name: str = ""
    baseline_run_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict, description="provider, model, use_rag, strategy, top_k, "
                                                                      "llm_judge, judge{provider,model}, thresholds{}")


class RedTeamIn(BaseModel):
    provider: str = "mock"
    model: str = "mock-reasoner-1"
    scenario_ids: list[str] | None = None


@router.post("/evaluations/datasets", status_code=201)
async def create_dataset(body: DatasetIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("evaluations:run"))) -> dict:
    if (await db.execute(select(EvaluationDataset).where(EvaluationDataset.organization_id == p.org_id,
                                                         EvaluationDataset.name == body.name))).scalar_one_or_none():
        raise ConflictError("Dataset name already exists")
    ds = EvaluationDataset(id=new_id(), organization_id=p.org_id, name=body.name, description=body.description, kind=body.kind)
    db.add(ds)
    for c in body.cases:
        db.add(EvaluationCase(dataset_id=ds.id, **c.model_dump()))
    await db.flush()
    await audit.record(db, org_id=p.org_id, action="evaluation.dataset_create", category="evaluation", actor_id=p.user_id,
                       actor_email=p.email, resource_type="evaluation_dataset", resource_id=ds.id, details={"cases": len(body.cases)})
    return to_dict(ds)


@router.get("/evaluations/datasets")
async def list_datasets(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("evaluations:read"))) -> list[dict]:
    rows = (await db.execute(select(EvaluationDataset, func.count(EvaluationCase.id)).outerjoin(EvaluationCase)
                             .where(EvaluationDataset.organization_id == p.org_id).group_by(EvaluationDataset.id)
                             .order_by(EvaluationDataset.name))).all()
    return [{**to_dict(d), "case_count": n} for d, n in rows]


@router.get("/evaluations/datasets/{dataset_id}")
async def get_dataset(dataset_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("evaluations:read"))) -> dict:
    ds = await get_owned(db, EvaluationDataset, dataset_id, p.org_id)
    cases = (await db.execute(select(EvaluationCase).where(EvaluationCase.dataset_id == ds.id).order_by(EvaluationCase.created_at))).scalars()
    return {**to_dict(ds), "cases": [to_dict(c) for c in cases]}


@router.post("/evaluations/datasets/{dataset_id}/cases", status_code=201)
async def add_case(dataset_id: str, body: CaseIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("evaluations:run"))) -> dict:
    ds = await get_owned(db, EvaluationDataset, dataset_id, p.org_id)
    c = EvaluationCase(dataset_id=ds.id, **body.model_dump())
    db.add(c)
    await db.flush()
    return to_dict(c)


@router.delete("/evaluations/datasets/{dataset_id}/cases/{case_id}")
async def delete_case(dataset_id: str, case_id: str, db: AsyncSession = Depends(get_db),
                      p: Principal = Depends(require("evaluations:run"))) -> dict:
    ds = await get_owned(db, EvaluationDataset, dataset_id, p.org_id)
    c = (await db.execute(select(EvaluationCase).where(EvaluationCase.id == case_id, EvaluationCase.dataset_id == ds.id))).scalar_one_or_none()
    if c is None:
        raise NotFoundError("Case not found")
    await db.delete(c)
    return {"ok": True}


@router.post("/evaluations/run", status_code=202, summary="Run an evaluation in the background")
async def run_evaluation(body: RunIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("evaluations:run"))) -> dict:
    ds = await get_owned(db, EvaluationDataset, body.dataset_id, p.org_id)
    if body.target_type in ("prompt", "agent") and not body.target_id:
        raise ConflictError("target_id is required for prompt/agent targets")
    if body.target_type == "prompt" and body.target_version is None:
        from app.models import PromptTemplate

        t = await get_owned(db, PromptTemplate, body.target_id, p.org_id)
        body.target_version = t.current_version
    if body.baseline_run_id:
        await get_owned(db, EvaluationRun, body.baseline_run_id, p.org_id)
    run = EvaluationRun(id=new_id(), organization_id=p.org_id, dataset_id=ds.id, name=body.name or f"{body.target_type} eval",
                        target_type=body.target_type, target_id=body.target_id, target_version=body.target_version,
                        config=body.config, baseline_run_id=body.baseline_run_id, created_by=p.user_id, status="queued")
    db.add(run)
    await db.commit()
    await jobs.enqueue("run_evaluation", p, run_id=run.id)
    return to_dict(run)


@router.get("/evaluations/runs")
async def list_runs(target_type: str | None = None, page: Pagination = Depends(), db: AsyncSession = Depends(get_db),
                    p: Principal = Depends(require("evaluations:read"))) -> dict:
    stmt = select(EvaluationRun).where(EvaluationRun.organization_id == p.org_id)
    if target_type:
        stmt = stmt.where(EvaluationRun.target_type == target_type)
    else:
        stmt = stmt.where(EvaluationRun.target_type != "redteam")
    return await paginate(db, stmt.order_by(EvaluationRun.created_at.desc()), page)


@router.get("/evaluations/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("evaluations:read"))) -> dict:
    return to_dict(await get_owned(db, EvaluationRun, run_id, p.org_id))


@router.get("/evaluations/{run_id}/results", summary="Per-case results + report")
async def run_results(run_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("evaluations:read"))) -> dict:
    run = await get_owned(db, EvaluationRun, run_id, p.org_id)
    rows = (await db.execute(select(EvaluationResult, EvaluationCase).join(EvaluationCase, EvaluationCase.id == EvaluationResult.case_id)
                             .where(EvaluationResult.run_id == run.id).order_by(EvaluationResult.created_at))).all()
    return {"run": to_dict(run), "thresholds": {**DEFAULT_THRESHOLDS, **((run.config or {}).get("thresholds") or {})},
            "judge_limitations": JUDGE_LIMITATIONS,
            "results": [{**to_dict(r), "case": {"input": c.input, "reference_answer": c.reference_answer,
                                                "expected_citations": c.expected_citations, "tags": c.tags}} for r, c in rows]}


# ------------------------------------------------------------------ red team ------------------
@router.get("/redteam/scenarios")
async def scenarios(p: Principal = Depends(require("evaluations:read"))) -> list[dict]:
    return [{"id": s.id, "category": s.category, "title": s.title, "tests": s.tests, "description": s.description}
            for s in SCENARIOS]


@router.post("/redteam/run", status_code=202, summary="Run the adversarial suite in the sandbox")
async def run_redteam(body: RedTeamIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("redteam:run"))) -> dict:
    if not get_settings().flag("red_team_lab"):
        raise FeatureDisabledError("Red Team Lab is disabled")
    ds = (await db.execute(select(EvaluationDataset).where(EvaluationDataset.organization_id == p.org_id,
                                                           EvaluationDataset.kind == "redteam"))).scalars().first()
    if ds is None:
        ds = EvaluationDataset(id=new_id(), organization_id=p.org_id, name="Red Team Scenario Library", kind="redteam",
                               description="Built-in adversarial scenarios (sandboxed)")
        db.add(ds)
    run = EvaluationRun(id=new_id(), organization_id=p.org_id, dataset_id=ds.id, name=f"red team ({body.provider})",
                        target_type="redteam", config=body.model_dump(), created_by=p.user_id, status="queued")
    db.add(run)
    await db.commit()
    await jobs.enqueue("run_redteam", p, run_id=run.id, provider=body.provider, model=body.model, scenario_ids=body.scenario_ids)
    return to_dict(run)


@router.get("/redteam/runs")
async def redteam_runs(page: Pagination = Depends(), db: AsyncSession = Depends(get_db),
                       p: Principal = Depends(require("evaluations:read"))) -> dict:
    stmt = select(EvaluationRun).where(EvaluationRun.organization_id == p.org_id, EvaluationRun.target_type == "redteam")
    return await paginate(db, stmt.order_by(EvaluationRun.created_at.desc()), page)
