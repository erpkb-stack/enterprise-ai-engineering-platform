from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import Pagination, get_owned, paginate, to_dict
from app.core.db import get_db
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.ids import new_id, utcnow
from app.models import PromptTemplate, PromptVersion
from app.security import audit
from app.security.principal import Principal, require
from app.services import prompt_service

router = APIRouter(prefix="/prompts", tags=["prompts"])


class VersionIn(BaseModel):
    system: str = Field("", max_length=50_000)
    developer: str = Field("", max_length=50_000)
    user_template: str = Field(min_length=1, max_length=50_000)
    model_config_json: dict[str, Any] = Field(default_factory=lambda: {"provider": "mock", "model": "mock-reasoner-1",
                                                                         "temperature": 0.2, "max_tokens": 700},
                                              alias="model_config")
    output_schema: dict[str, Any] | None = None
    change_note: str = Field("", max_length=500)

    model_config = {"populate_by_name": True, "protected_namespaces": ()}


class TemplateIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    version: VersionIn


class TestIn(BaseModel):
    version: int | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    context: list[str] | None = None
    use_rag: bool = False
    collection: str | None = None
    provider: str | None = None
    model: str | None = None
    repeats: int = Field(1, ge=1, le=5)
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    reference: str | None = None


class CompareIn(BaseModel):
    version_a: int
    version_b: int
    variables: dict[str, Any] = Field(default_factory=dict)
    context: list[str] | None = None
    use_rag: bool = False


class ReviewIn(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str = Field("", max_length=2000)


def _ver(v: PromptVersion) -> dict:
    d = to_dict(v)
    d["model_config"] = d.pop("model_config_json")
    d["variables"] = prompt_service.declared_variables(v.system, v.developer, v.user_template)
    return d


async def _version(db: AsyncSession, t: PromptTemplate, version: int | None) -> PromptVersion:
    n = version or t.current_version
    v = (await db.execute(select(PromptVersion).where(PromptVersion.template_id == t.id, PromptVersion.version == n))).scalar_one_or_none()
    if v is None:
        raise NotFoundError(f"Version {n} not found")
    return v


@router.post("", status_code=201)
async def create_template(body: TemplateIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("prompts:write"))) -> dict:
    if (await db.execute(select(PromptTemplate).where(PromptTemplate.organization_id == p.org_id, PromptTemplate.name == body.name))).scalar_one_or_none():
        raise ConflictError("Prompt name already exists")
    variables = prompt_service.declared_variables(body.version.system, body.version.developer, body.version.user_template)
    t = PromptTemplate(id=new_id(), organization_id=p.org_id, name=body.name, description=body.description, tags=body.tags,
                       current_version=1, created_by=p.user_id)
    db.add(t)
    v = PromptVersion(template_id=t.id, version=1, variables=variables, created_by=p.user_id,
                      **body.version.model_dump(exclude={"change_note"}), change_note=body.version.change_note or "initial")
    db.add(v)
    await db.flush()
    await audit.record(db, org_id=p.org_id, action="prompt.create", category="prompt_change", actor_id=p.user_id,
                       actor_email=p.email, resource_type="prompt", resource_id=t.id,
                       details={"name": t.name, "system": body.version.system, "user_template": body.version.user_template})
    return {**to_dict(t), "versions": [_ver(v)]}


@router.get("")
async def list_templates(page: Pagination = Depends(), db: AsyncSession = Depends(get_db),
                         p: Principal = Depends(require("prompts:read"))) -> dict:
    return await paginate(db, select(PromptTemplate).where(PromptTemplate.organization_id == p.org_id)
                          .order_by(PromptTemplate.updated_at.desc()), page)


@router.get("/{template_id}")
async def get_template(template_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("prompts:read"))) -> dict:
    t = await get_owned(db, PromptTemplate, template_id, p.org_id)
    vs = (await db.execute(select(PromptVersion).where(PromptVersion.template_id == t.id).order_by(PromptVersion.version.desc()))).scalars()
    return {**to_dict(t), "versions": [_ver(v) for v in vs]}


@router.post("/{template_id}/versions", status_code=201)
async def new_version(template_id: str, body: VersionIn, db: AsyncSession = Depends(get_db),
                      p: Principal = Depends(require("prompts:write"))) -> dict:
    t = await get_owned(db, PromptTemplate, template_id, p.org_id)
    variables = prompt_service.declared_variables(body.system, body.developer, body.user_template)
    t.current_version += 1
    v = PromptVersion(template_id=t.id, version=t.current_version, variables=variables, created_by=p.user_id,
                      **body.model_dump(exclude={"change_note"}), change_note=body.change_note)
    db.add(v)
    await db.flush()
    await audit.record(db, org_id=p.org_id, action="prompt.version_create", category="prompt_change", actor_id=p.user_id,
                       actor_email=p.email, resource_type="prompt", resource_id=t.id,
                       details={"version": v.version, "system": body.system, "user_template": body.user_template})
    return _ver(v)


@router.post("/{template_id}/preview")
async def preview(template_id: str, body: TestIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("prompts:read"))) -> dict:
    t = await get_owned(db, PromptTemplate, template_id, p.org_id)
    return prompt_service.preview(await _version(db, t, body.version), body.variables)


@router.post("/{template_id}/test", summary="Run a prompt version with grounding/citation/structure/consistency checks")
async def test_prompt(template_id: str, body: TestIn, db: AsyncSession = Depends(get_db),
                      p: Principal = Depends(require("prompts:test"))) -> dict:
    t = await get_owned(db, PromptTemplate, template_id, p.org_id)
    v = await _version(db, t, body.version)
    res = await prompt_service.run_prompt(db, org_id=p.org_id, version=v, variables=body.variables, context=body.context,
                                          use_rag=body.use_rag, collection=body.collection, provider=body.provider,
                                          model=body.model, repeats=body.repeats, must_contain=body.must_contain,
                                          must_not_contain=body.must_not_contain, reference=body.reference)
    await audit.record(db, org_id=p.org_id, action="prompt.test", category="model_invocation", actor_id=p.user_id,
                       actor_email=p.email, resource_type="prompt", resource_id=t.id,
                       details={"version": v.version, "provider": res["provider"], "model": res["model"],
                                "tokens": res["tokens_in"] + res["tokens_out"]})
    return {**res, "version": v.version}


@router.post("/{template_id}/compare", summary="Side-by-side execution of two versions on identical inputs")
async def compare(template_id: str, body: CompareIn, db: AsyncSession = Depends(get_db),
                  p: Principal = Depends(require("prompts:test"))) -> dict:
    t = await get_owned(db, PromptTemplate, template_id, p.org_id)
    out = {}
    for label, n in (("a", body.version_a), ("b", body.version_b)):
        v = await _version(db, t, n)
        out[label] = {"version": n, **await prompt_service.run_prompt(db, org_id=p.org_id, version=v, variables=body.variables,
                                                                       context=body.context, use_rag=body.use_rag)}
    return out


@router.post("/{template_id}/versions/{version}/submit")
async def submit(template_id: str, version: int, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("prompts:write"))) -> dict:
    t = await get_owned(db, PromptTemplate, template_id, p.org_id)
    v = await _version(db, t, version)
    if v.status not in ("draft", "rejected"):
        raise ConflictError(f"Version is {v.status}")
    v.status = "in_review"
    await audit.record(db, org_id=p.org_id, action="prompt.submit_review", category="prompt_change", actor_id=p.user_id,
                       actor_email=p.email, resource_type="prompt", resource_id=t.id, details={"version": version})
    return _ver(v)


@router.post("/{template_id}/versions/{version}/review", summary="Approve/reject (separation of duties enforced)")
async def review(template_id: str, version: int, body: ReviewIn, db: AsyncSession = Depends(get_db),
                 p: Principal = Depends(require("prompts:approve"))) -> dict:
    t = await get_owned(db, PromptTemplate, template_id, p.org_id)
    v = await _version(db, t, version)
    if v.status != "in_review":
        raise ConflictError("Only versions in review can be approved or rejected")
    if v.created_by == p.user_id:
        raise PermissionDeniedError("Authors cannot approve their own prompt versions (separation of duties)")
    v.status, v.reviewed_by, v.reviewed_at, v.review_comment = body.decision, p.user_id, utcnow(), body.comment
    if body.decision == "approved":
        t.approved_version = version
    await audit.record(db, org_id=p.org_id, action=f"prompt.{body.decision}", category="prompt_change", actor_id=p.user_id,
                       actor_email=p.email, resource_type="prompt", resource_id=t.id, details={"version": version})
    return _ver(v)
