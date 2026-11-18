from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import get_owned, to_dict
from app.core.db import get_db
from app.core.errors import ConflictError
from app.core.ids import new_id
from app.experiments import service
from app.models import Experiment, ExperimentVariant
from app.security import audit
from app.security.principal import Principal, require

router = APIRouter(prefix="/experiments", tags=["experiments"])


class VariantIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    is_control: bool = False
    traffic_weight: float = Field(50, ge=0, le=100)
    config: dict[str, Any] = Field(default_factory=dict)


class ExperimentIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    hypothesis: str = ""
    dimension: Literal["prompt", "model", "retrieval", "agent"]
    primary_metric: str = "quality_score"
    min_sample_size: int = Field(30, ge=2, le=100_000)
    dataset_id: str | None = None
    variants: list[VariantIn] = Field(min_length=2, max_length=6)

    @model_validator(mode="after")
    def _one_control(self) -> ExperimentIn:
        if sum(v.is_control for v in self.variants) != 1:
            raise ValueError("exactly one variant must be the control")
        if len({v.name for v in self.variants}) != len(self.variants):
            raise ValueError("variant names must be unique")
        return self


class RunIn(BaseModel):
    mode: Literal["paired", "split"] = "paired"
    repeats: int = Field(1, ge=1, le=10)


@router.post("", status_code=201)
async def create(body: ExperimentIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("experiments:write"))) -> dict:
    if (await db.execute(select(Experiment).where(Experiment.organization_id == p.org_id, Experiment.name == body.name))).scalar_one_or_none():
        raise ConflictError("Experiment name already exists")
    e = Experiment(id=new_id(), organization_id=p.org_id, created_by=p.user_id, **body.model_dump(exclude={"variants"}))
    db.add(e)
    for v in body.variants:
        db.add(ExperimentVariant(experiment_id=e.id, **v.model_dump()))
    await db.flush()
    await audit.record(db, org_id=p.org_id, action="experiment.create", category="evaluation", actor_id=p.user_id,
                       actor_email=p.email, resource_type="experiment", resource_id=e.id, details={"dimension": e.dimension})
    return await get(e.id, db, p)


@router.get("")
async def list_experiments(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("experiments:read"))) -> list[dict]:
    rows = (await db.execute(select(Experiment).where(Experiment.organization_id == p.org_id).order_by(Experiment.updated_at.desc()))).scalars()
    return [to_dict(e) for e in rows]


@router.get("/{experiment_id}")
async def get(experiment_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("experiments:read"))) -> dict:
    e = await get_owned(db, Experiment, experiment_id, p.org_id)
    vs = (await db.execute(select(ExperimentVariant).where(ExperimentVariant.experiment_id == e.id))).scalars()
    return {**to_dict(e), "variants": [to_dict(v) for v in vs]}


@router.post("/{experiment_id}/run", summary="Execute variants over the attached dataset")
async def run(experiment_id: str, body: RunIn, db: AsyncSession = Depends(get_db),
              p: Principal = Depends(require("experiments:write"))) -> dict:
    e = await get_owned(db, Experiment, experiment_id, p.org_id)
    res = await service.run(db, e, p, mode=body.mode, repeats=body.repeats)
    await audit.record(db, org_id=p.org_id, action="experiment.run", category="evaluation", actor_id=p.user_id,
                       actor_email=p.email, resource_type="experiment", resource_id=e.id, details=res)
    return {**res, "results": await service.results(db, e)}


@router.get("/{experiment_id}/results")
async def results(experiment_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("experiments:read"))) -> dict:
    return await service.results(db, await get_owned(db, Experiment, experiment_id, p.org_id))


@router.get("/{experiment_id}/assign/{unit_id}", summary="Deterministic traffic assignment for a unit (user/session)")
async def assign(experiment_id: str, unit_id: str, db: AsyncSession = Depends(get_db),
                 p: Principal = Depends(require("experiments:read"))) -> dict:
    e = await get_owned(db, Experiment, experiment_id, p.org_id)
    vs = (await db.execute(select(ExperimentVariant).where(ExperimentVariant.experiment_id == e.id))).scalars().all()
    v = service.assign_variant(e.id, unit_id, list(vs))
    return {"unit_id": unit_id, "variant": v.name, "variant_id": v.id}
