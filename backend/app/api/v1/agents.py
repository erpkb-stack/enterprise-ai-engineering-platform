from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import runtime
from app.api.common import Pagination, get_owned, idempotent_lookup, idempotent_store, paginate, to_dict
from app.core.db import get_db
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.ids import new_id
from app.domain.agents import ALLOWED_TRANSITIONS, AgentConfig, AgentCreate, AgentExecuteRequest, AgentUpdate
from app.models import Agent, AgentExecution, AgentVersion
from app.security import audit
from app.security.principal import Principal, require

router = APIRouter(prefix="/agents", tags=["agents"])


class LifecycleRequest(BaseModel):
    status: str = Field(pattern="^(active|inactive|archived)$")


class CloneRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)


async def _agent_out(db: AsyncSession, a: Agent) -> dict:
    v = (await db.execute(select(AgentVersion).where(AgentVersion.agent_id == a.id, AgentVersion.version == a.current_version))).scalar_one_or_none()
    return {**to_dict(a), "config": v.config if v else None}


@router.post("", status_code=201, summary="Create an agent (version 1, status draft)")
async def create_agent(body: AgentCreate, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("agents:write"))) -> dict:
    if (await db.execute(select(Agent).where(Agent.organization_id == p.org_id, Agent.name == body.name))).scalar_one_or_none():
        raise ConflictError("Agent name already exists")
    if body.config.collaborators and body.agent_type != "orchestrator":
        raise ValidationFailedError("Only orchestrator agents can declare collaborators")
    a = Agent(id=new_id(), organization_id=p.org_id, name=body.name, agent_type=body.agent_type,
              description=body.description, team_id=body.team_id, status="draft", current_version=1, created_by=p.user_id)
    db.add(a)
    db.add(AgentVersion(agent_id=a.id, version=1, config=body.config.model_dump(), change_note="initial", created_by=p.user_id))
    await db.flush()
    await audit.record(db, org_id=p.org_id, action="agent.create", category="agent_config", actor_id=p.user_id,
                       actor_email=p.email, resource_type="agent", resource_id=a.id,
                       details={"name": a.name, "type": a.agent_type, "tools": body.config.tools})
    return await _agent_out(db, a)


@router.get("")
async def list_agents(status: str | None = None, agent_type: str | None = None, q: str | None = None,
                      page: Pagination = Depends(), db: AsyncSession = Depends(get_db),
                      p: Principal = Depends(require("agents:read"))) -> dict:
    stmt = select(Agent).where(Agent.organization_id == p.org_id)
    if status:
        stmt = stmt.where(Agent.status == status)
    else:
        stmt = stmt.where(Agent.status != "archived")
    if agent_type:
        stmt = stmt.where(Agent.agent_type == agent_type)
    if q:
        stmt = stmt.where(Agent.name.ilike(f"%{q}%"))
    res = await paginate(db, stmt.order_by(Agent.updated_at.desc()), page, ser=lambda a: a)
    agents: list[Agent] = res["items"]
    # one query for all current versions (avoids N+1)
    pairs = [(a.id, a.current_version) for a in agents]
    versions = {}
    if pairs:
        rows = (await db.execute(select(AgentVersion).where(tuple_(AgentVersion.agent_id, AgentVersion.version).in_(pairs)))).scalars()
        versions = {v.agent_id: v.config for v in rows}
    res["items"] = [{**to_dict(a), "config": versions.get(a.id)} for a in agents]
    return res


@router.get("/{agent_id}")
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("agents:read"))) -> dict:
    return await _agent_out(db, await get_owned(db, Agent, agent_id, p.org_id))


@router.put("/{agent_id}", summary="Update agent; config changes create a new immutable version")
async def update_agent(agent_id: str, body: AgentUpdate, db: AsyncSession = Depends(get_db),
                       p: Principal = Depends(require("agents:write"))) -> dict:
    a = await get_owned(db, Agent, agent_id, p.org_id)
    if a.status == "archived":
        raise ConflictError("Archived agents are read-only")
    changes: dict = {}
    if body.name and body.name != a.name:
        changes["name"] = [a.name, body.name]
        a.name = body.name
    if body.description is not None:
        a.description = body.description
    if body.config is not None:
        if body.config.collaborators and a.agent_type != "orchestrator":
            raise ValidationFailedError("Only orchestrator agents can declare collaborators")
        cur = (await db.execute(select(AgentVersion).where(AgentVersion.agent_id == a.id,
                                                           AgentVersion.version == a.current_version))).scalar_one()
        new_cfg = body.config.model_dump()
        if new_cfg != cur.config:
            a.current_version += 1
            db.add(AgentVersion(agent_id=a.id, version=a.current_version, config=new_cfg, change_note=body.change_note,
                                created_by=p.user_id))
            changes["config_keys"] = sorted(k for k in new_cfg if new_cfg[k] != cur.config.get(k))
            changes["version"] = a.current_version
    await db.flush()
    await audit.record(db, org_id=p.org_id, action="agent.update", category="agent_config", actor_id=p.user_id,
                       actor_email=p.email, resource_type="agent", resource_id=a.id, details=changes)
    return await _agent_out(db, a)


@router.post("/{agent_id}/clone", status_code=201)
async def clone_agent(agent_id: str, body: CloneRequest, db: AsyncSession = Depends(get_db),
                      p: Principal = Depends(require("agents:write"))) -> dict:
    src = await get_owned(db, Agent, agent_id, p.org_id)
    v = (await db.execute(select(AgentVersion).where(AgentVersion.agent_id == src.id, AgentVersion.version == src.current_version))).scalar_one()
    return await create_agent(AgentCreate(name=body.name, agent_type=src.agent_type, description=src.description,
                                          team_id=src.team_id, config=AgentConfig.model_validate(v.config)), db, p)


@router.post("/{agent_id}/lifecycle", summary="Transition draft/active/inactive/archived")
async def lifecycle(agent_id: str, body: LifecycleRequest, db: AsyncSession = Depends(get_db),
                    p: Principal = Depends(require("agents:lifecycle"))) -> dict:
    a = await get_owned(db, Agent, agent_id, p.org_id)
    if body.status not in ALLOWED_TRANSITIONS[a.status]:
        raise ConflictError(f"Cannot transition from '{a.status}' to '{body.status}'",
                            details={"allowed": sorted(ALLOWED_TRANSITIONS[a.status])})
    before = a.status
    a.status = body.status
    await audit.record(db, org_id=p.org_id, action=f"agent.{body.status}", category="agent_config", actor_id=p.user_id,
                       actor_email=p.email, resource_type="agent", resource_id=a.id, details={"from": before, "to": body.status})
    return await _agent_out(db, a)


@router.get("/{agent_id}/versions")
async def versions(agent_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("agents:read"))) -> list[dict]:
    a = await get_owned(db, Agent, agent_id, p.org_id)
    rows = (await db.execute(select(AgentVersion).where(AgentVersion.agent_id == a.id).order_by(AgentVersion.version.desc()))).scalars()
    return [to_dict(v) for v in rows]


@router.post("/{agent_id}/execute", summary="Execute an agent (sync waits; async returns 202). Honors Idempotency-Key.")
async def execute_agent(agent_id: str, body: AgentExecuteRequest, request: Request, response: Response,
                        db: AsyncSession = Depends(get_db), p: Principal = Depends(require("agents:execute"))) -> dict:
    ck, cached = await idempotent_lookup(request, p.org_id)
    if cached:
        response.headers["Idempotent-Replay"] = "true"
        return cached
    a = await get_owned(db, Agent, agent_id, p.org_id)
    if a.status != "active":
        raise ConflictError(f"Agent is '{a.status}'; only active agents can be executed")
    ex = await runtime.create_execution(db, org_id=p.org_id, agent=a, principal=p, input_text=body.input, version=body.version)
    await db.commit()
    await runtime.execute(ex.id, p, wait=body.mode == "sync")
    if body.mode == "async":
        response.status_code = 202
        out = {"id": ex.id, "status": "queued", "agent_id": a.id}
    else:
        row = (await db.execute(select(AgentExecution).where(AgentExecution.id == ex.id)
                                .execution_options(populate_existing=True))).scalar_one()
        out = to_dict(row)
    await idempotent_store(ck, out)
    return out


@router.get("/{agent_id}/executions")
async def list_executions(agent_id: str, page: Pagination = Depends(), db: AsyncSession = Depends(get_db),
                          p: Principal = Depends(require("agents:read"))) -> dict:
    await get_owned(db, Agent, agent_id, p.org_id)
    stmt = select(AgentExecution).where(AgentExecution.organization_id == p.org_id, AgentExecution.agent_id == agent_id)
    return await paginate(db, stmt.order_by(AgentExecution.created_at.desc()), page,
                          ser=lambda e: to_dict(e, exclude={"steps"}))


executions_router = APIRouter(prefix="/executions", tags=["agents"])


@executions_router.get("")
async def all_executions(status: str | None = None, page: Pagination = Depends(), db: AsyncSession = Depends(get_db),
                         p: Principal = Depends(require("agents:read"))) -> dict:
    stmt = select(AgentExecution).where(AgentExecution.organization_id == p.org_id)
    if status:
        stmt = stmt.where(AgentExecution.status == status)
    return await paginate(db, stmt.order_by(AgentExecution.created_at.desc()), page,
                          ser=lambda e: to_dict(e, exclude={"steps"}))


@executions_router.get("/{execution_id}")
async def get_execution(execution_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("agents:read"))) -> dict:
    ex = await get_owned(db, AgentExecution, execution_id, p.org_id)
    children = (await db.execute(select(AgentExecution.id).where(AgentExecution.parent_execution_id == ex.id))).scalars().all()
    return {**to_dict(ex), "children": list(children)}


@executions_router.post("/{execution_id}/cancel")
async def cancel_execution(execution_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("agents:execute"))) -> dict:
    ex = await get_owned(db, AgentExecution, execution_id, p.org_id)
    if ex.status not in ("queued", "running"):
        raise ConflictError(f"Execution already {ex.status}")
    ok = await runtime.cancel(execution_id)
    if not ok:
        raise NotFoundError("Execution is not running on this instance")
    await audit.record(db, org_id=p.org_id, action="agent.execution_cancelled", category="model_invocation",
                       actor_id=p.user_id, actor_email=p.email, resource_type="agent_execution", resource_id=execution_id)
    return {"ok": True, "status": "cancelled"}
