"""Architecture Lab and Code Review Assistant."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import get_owned, to_dict
from app.architecture import review as arch
from app.codereview import analyzer
from app.core.db import get_db
from app.core.errors import ConflictError
from app.core.ids import new_id
from app.integrations.llm.base import ChatMessage, CompletionRequest
from app.integrations.llm.gateway import Route, gateway
from app.models import ArchitectureDecisionRecord, ArchitectureDesign, ArchitectureReview, CodeReview
from app.security import audit
from app.security.principal import Principal, require

router = APIRouter(tags=["engineering"])


class DesignIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str = ""
    kind: str = "microservices"
    graph: dict[str, Any] = Field(default_factory=lambda: {"nodes": [], "edges": []})
    sequence_diagram: str = ""
    data_flow: str = ""
    api_contracts: str = ""
    requirements: dict[str, Any] = Field(default_factory=dict)


class DesignPatch(BaseModel):
    description: str | None = None
    graph: dict[str, Any] | None = None
    sequence_diagram: str | None = None
    data_flow: str | None = None
    api_contracts: str | None = None
    requirements: dict[str, Any] | None = None


class ReviewIn(BaseModel):
    ai_narrative: bool = False
    provider: str = "mock"
    model: str = "mock-reasoner-1"


class AdrIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    status: str = Field("proposed", pattern="^(proposed|accepted|superseded|deprecated|rejected)$")
    context: str = ""
    decision: str = ""
    consequences: str = ""
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    design_id: str | None = None


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=200_000)
    filename: str = "snippet.py"
    ai_review: bool = True
    provider: str = "mock"
    model: str = "mock-reasoner-1"


@router.post("/architecture/designs", status_code=201)
async def create_design(body: DesignIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("architecture:write"))) -> dict:
    if (await db.execute(select(ArchitectureDesign).where(ArchitectureDesign.organization_id == p.org_id,
                                                          ArchitectureDesign.name == body.name))).scalar_one_or_none():
        raise ConflictError("Design name already exists")
    d = ArchitectureDesign(id=new_id(), organization_id=p.org_id, created_by=p.user_id, **body.model_dump())
    db.add(d)
    await db.flush()
    return to_dict(d)


@router.get("/architecture/designs")
async def list_designs(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("architecture:read"))) -> list[dict]:
    rows = (await db.execute(select(ArchitectureDesign).where(ArchitectureDesign.organization_id == p.org_id)
                             .order_by(ArchitectureDesign.updated_at.desc()))).scalars()
    return [to_dict(d) for d in rows]


@router.get("/architecture/designs/{design_id}")
async def get_design(design_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("architecture:read"))) -> dict:
    d = await get_owned(db, ArchitectureDesign, design_id, p.org_id)
    reviews = (await db.execute(select(ArchitectureReview).where(ArchitectureReview.design_id == d.id)
                                .order_by(ArchitectureReview.created_at.desc()).limit(10))).scalars()
    return {**to_dict(d), "reviews": [to_dict(r) for r in reviews]}


@router.put("/architecture/designs/{design_id}")
async def update_design(design_id: str, body: DesignPatch, db: AsyncSession = Depends(get_db),
                        p: Principal = Depends(require("architecture:write"))) -> dict:
    d = await get_owned(db, ArchitectureDesign, design_id, p.org_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(d, k, v)
    return to_dict(d)


@router.post("/architecture/designs/{design_id}/review", summary="Run the Architecture Review Assistant")
async def review_design(design_id: str, body: ReviewIn, db: AsyncSession = Depends(get_db),
                        p: Principal = Depends(require("architecture:read"))) -> dict:
    d = await get_owned(db, ArchitectureDesign, design_id, p.org_id)
    report = arch.review(d.graph, d.requirements)
    if body.ai_narrative:
        lines = ["Scores: " + ", ".join(f"{k} {v}/100" for k, v in report["scores"].items()) + "."]
        lines += [f"{f['severity'].upper()} {f['category']} risk: {f['title']}. Recommendation: {f['recommendation']}"
                  for f in report["findings"][:8]]
        top = "\n".join(lines)[:6000]
        resp, meta = await gateway.complete(CompletionRequest(model=body.model, temperature=0.2, max_tokens=600, messages=[
            ChatMessage(role="system", content="You are a principal architect. Summarise the key risks and the most "
                                               "important tradeoff decisions in 5 bullet points."),
            ChatMessage(role="user", content=f"[1] {top}\n\nSummarise this architecture review.")]),
            route=Route(body.provider, body.model), db=db, org_id=p.org_id, purpose="architecture_review")
        report["ai_narrative"] = {"text": resp.content, "is_mock": resp.is_mock, "model": meta["model"],
                                  "label": "AI-generated summary - verify against deterministic findings"}
    r = ArchitectureReview(organization_id=p.org_id, design_id=d.id, report=report, created_by=p.user_id)
    db.add(r)
    await db.flush()
    return {**report, "review_id": r.id}


@router.post("/architecture/designs/{design_id}/sequence", summary="Generate a Mermaid sequence diagram from edges")
async def gen_sequence(design_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("architecture:write"))) -> dict:
    d = await get_owned(db, ArchitectureDesign, design_id, p.org_id)
    d.sequence_diagram = arch.sequence_from_graph(d.graph)
    return {"sequence_diagram": d.sequence_diagram}


@router.get("/architecture/adrs")
async def list_adrs(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("architecture:read"))) -> list[dict]:
    rows = (await db.execute(select(ArchitectureDecisionRecord).where(ArchitectureDecisionRecord.organization_id == p.org_id)
                             .order_by(ArchitectureDecisionRecord.number))).scalars()
    return [to_dict(a) for a in rows]


@router.post("/architecture/adrs", status_code=201)
async def create_adr(body: AdrIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("architecture:write"))) -> dict:
    n = (await db.execute(select(func.coalesce(func.max(ArchitectureDecisionRecord.number), 0))
                          .where(ArchitectureDecisionRecord.organization_id == p.org_id))).scalar_one() + 1
    a = ArchitectureDecisionRecord(organization_id=p.org_id, number=n, created_by=p.user_id, **body.model_dump())
    db.add(a)
    await db.flush()
    await audit.record(db, org_id=p.org_id, action="architecture.adr_create", category="deployment", actor_id=p.user_id,
                       actor_email=p.email, resource_type="adr", resource_id=a.id, details={"number": n, "title": a.title})
    return to_dict(a)


@router.put("/architecture/adrs/{adr_id}")
async def update_adr(adr_id: str, body: AdrIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("architecture:write"))) -> dict:
    a = await get_owned(db, ArchitectureDecisionRecord, adr_id, p.org_id)
    for k, v in body.model_dump().items():
        setattr(a, k, v)
    return to_dict(a)


CODE_REVIEW_SYSTEM = (
    "AIECC_CODE_REVIEW. You are a staff Python engineer. Given code and static-analysis findings, return JSON: "
    '{"summary": str, "suggestions": [{"title": str, "detail": str, "confidence": "low|medium|high"}], '
    '"generated_tests": str}. Focus on concurrency, async correctness, API contracts, performance and architecture.'
)


@router.post("/code-review", summary="Static analysis (deterministic) + optional AI review (labelled)")
async def code_review(body: CodeIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("codereview:run"))) -> dict:
    static = analyzer.analyze(body.code)
    ai: dict[str, Any] = {}
    if body.ai_review:
        findings = json.dumps(static["findings"][:20])
        try:
            resp, meta = await gateway.complete(CompletionRequest(model=body.model, temperature=0.1, max_tokens=1500,
                                                                  response_format="json", messages=[
                ChatMessage(role="system", content=CODE_REVIEW_SYSTEM),
                ChatMessage(role="user", content=f"{body.code[:30_000]}\n\nSTATIC FINDINGS: {findings}")]),
                route=Route(body.provider, body.model), db=db, org_id=p.org_id, purpose="code_review")
            txt = resp.content
            try:
                parsed = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
            except (json.JSONDecodeError, ValueError):
                parsed = {"summary": txt, "suggestions": [], "generated_tests": ""}
            ai = {**parsed, "source": "ai-generated", "is_mock": resp.is_mock, "model": meta["model"],
                  "provider": meta["provider"], "disclaimer": "AI suggestions are not verified; validate before applying."}
        except Exception as exc:  # noqa: BLE001
            ai = {"error": str(exc)[:300], "source": "ai-generated"}
    cr = CodeReview(organization_id=p.org_id, filename=body.filename, code=body.code, static_findings=static["findings"],
                    ai_review=ai, metrics=static["metrics"], created_by=p.user_id)
    db.add(cr)
    await db.flush()
    return {"id": cr.id, "static": static, "ai": ai, "signatures": analyzer.function_signatures(body.code)}


@router.get("/code-review")
async def code_reviews(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("codereview:run"))) -> list[dict]:
    rows = (await db.execute(select(CodeReview).where(CodeReview.organization_id == p.org_id)
                             .order_by(CodeReview.created_at.desc()).limit(25))).scalars()
    return [to_dict(r, exclude={"code"}) for r in rows]
