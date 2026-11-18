from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import Pagination, get_owned, paginate, to_dict
from app.core.db import get_db
from app.core.errors import ConflictError
from app.core.ids import new_id, utcnow
from app.models import UsageMetric, Workflow, WorkflowExecution, WorkflowVersion
from app.security import audit
from app.security.principal import Principal, require
from app.workflows import engine

router = APIRouter(prefix="/workflows", tags=["workflows"])


class WorkflowIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str = ""
    graph: dict[str, Any]


class WorkflowUpdate(BaseModel):
    description: str | None = None
    graph: dict[str, Any] | None = None
    change_note: str = ""


class ExecuteIn(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None
    breakpoints: list[str] = Field(default_factory=list)
    step_mode: bool = False


class ApprovalIn(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str = ""


class ResumeIn(BaseModel):
    step: bool = False


async def _graph(db: AsyncSession, wf: Workflow, version: int) -> dict:
    v = (await db.execute(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf.id, WorkflowVersion.version == version))).scalar_one()
    return v.graph


@router.post("/validate", summary="Validate a graph without saving")
async def validate(body: WorkflowIn, p: Principal = Depends(require("workflows:read"))) -> dict:
    errs = engine.validate_graph(body.graph)
    return {"valid": not errs, "errors": errs, "node_types": sorted(engine.NODE_TYPES),
            "python_functions": sorted(engine.PY_FUNCTIONS)}


@router.post("", status_code=201)
async def create(body: WorkflowIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("workflows:write"))) -> dict:
    engine.require_valid(body.graph)
    if (await db.execute(select(Workflow).where(Workflow.organization_id == p.org_id, Workflow.name == body.name))).scalar_one_or_none():
        raise ConflictError("Workflow name already exists")
    wf = Workflow(id=new_id(), organization_id=p.org_id, name=body.name, description=body.description, created_by=p.user_id)
    db.add(wf)
    db.add(WorkflowVersion(workflow_id=wf.id, version=1, graph=body.graph, change_note="initial", created_by=p.user_id))
    await db.flush()
    await audit.record(db, org_id=p.org_id, action="workflow.create", category="agent_config", actor_id=p.user_id,
                       actor_email=p.email, resource_type="workflow", resource_id=wf.id,
                       details={"nodes": len(body.graph.get("nodes", []))})
    return {**to_dict(wf), "graph": body.graph}


@router.get("")
async def list_workflows(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("workflows:read"))) -> list[dict]:
    rows = (await db.execute(select(Workflow).where(Workflow.organization_id == p.org_id).order_by(Workflow.updated_at.desc()))).scalars()
    return [to_dict(w) for w in rows]


@router.get("/{workflow_id}")
async def get(workflow_id: str, version: int | None = None, db: AsyncSession = Depends(get_db),
              p: Principal = Depends(require("workflows:read"))) -> dict:
    wf = await get_owned(db, Workflow, workflow_id, p.org_id)
    versions = (await db.execute(select(WorkflowVersion.version, WorkflowVersion.change_note, WorkflowVersion.created_at)
                                 .where(WorkflowVersion.workflow_id == wf.id).order_by(WorkflowVersion.version.desc()))).all()
    return {**to_dict(wf), "graph": await _graph(db, wf, version or wf.current_version),
            "versions": [{"version": v, "change_note": n, "created_at": c.isoformat()} for v, n, c in versions]}


@router.put("/{workflow_id}", summary="Save a new version")
async def update(workflow_id: str, body: WorkflowUpdate, db: AsyncSession = Depends(get_db),
                 p: Principal = Depends(require("workflows:write"))) -> dict:
    wf = await get_owned(db, Workflow, workflow_id, p.org_id)
    if body.description is not None:
        wf.description = body.description
    if body.graph is not None:
        engine.require_valid(body.graph)
        wf.current_version += 1
        db.add(WorkflowVersion(workflow_id=wf.id, version=wf.current_version, graph=body.graph, change_note=body.change_note,
                               created_by=p.user_id))
    await db.flush()
    await audit.record(db, org_id=p.org_id, action="workflow.update", category="agent_config", actor_id=p.user_id,
                       actor_email=p.email, resource_type="workflow", resource_id=wf.id, details={"version": wf.current_version})
    return await get(wf.id, None, db, p)


@router.post("/{workflow_id}/execute", summary="Execute (optionally with breakpoints / step mode)")
async def execute(workflow_id: str, body: ExecuteIn, db: AsyncSession = Depends(get_db),
                  p: Principal = Depends(require("workflows:execute"))) -> dict:
    wf = await get_owned(db, Workflow, workflow_id, p.org_id)
    version = body.version or wf.current_version
    graph = await _graph(db, wf, version)
    engine.require_valid(graph)
    ex = WorkflowExecution(id=new_id(), organization_id=p.org_id, workflow_id=wf.id, version=version, input=body.input,
                           state=engine.initial_state(graph), trace=[], breakpoints=body.breakpoints, created_by=p.user_id)
    db.add(ex)
    await db.commit()
    await engine.drive(db, ex, graph, p, step_once=body.step_mode)
    db.add(UsageMetric(organization_id=p.org_id, kind="workflow", name=wf.name, latency_ms=ex.latency_ms,
                       success=ex.status != "failed"))
    await audit.record(db, org_id=p.org_id, action="workflow.execute", category="model_invocation", actor_id=p.user_id,
                       actor_email=p.email, resource_type="workflow", resource_id=wf.id,
                       outcome="failure" if ex.status == "failed" else "success",
                       details={"execution_id": ex.id, "status": ex.status, "nodes_run": len(ex.trace)})
    return to_dict(ex)


@router.get("/{workflow_id}/executions")
async def executions(workflow_id: str, page: Pagination = Depends(), db: AsyncSession = Depends(get_db),
                     p: Principal = Depends(require("workflows:read"))) -> dict:
    await get_owned(db, Workflow, workflow_id, p.org_id)
    stmt = select(WorkflowExecution).where(WorkflowExecution.workflow_id == workflow_id,
                                           WorkflowExecution.organization_id == p.org_id)
    return await paginate(db, stmt.order_by(WorkflowExecution.created_at.desc()), page,
                          ser=lambda e: to_dict(e, exclude={"state"}))


wf_exec_router = APIRouter(prefix="/workflow-executions", tags=["workflows"])


@wf_exec_router.get("/{execution_id}")
async def get_execution(execution_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("workflows:read"))) -> dict:
    return to_dict(await get_owned(db, WorkflowExecution, execution_id, p.org_id))


async def _initiator(db: AsyncSession, ex: WorkflowExecution, fallback: Principal) -> Principal:
    """Resumed executions keep running with the INITIATOR's authority; approvers only authorize the step."""
    from app.models import User

    u = (await db.execute(select(User).where(User.id == ex.created_by))).scalar_one_or_none() if ex.created_by else None
    if u is None or not u.is_active:
        return fallback
    return Principal(u.id, u.organization_id, u.email, u.role_names, u.permission_codes, [t.id for t in u.teams])


async def _resume(db: AsyncSession, ex: WorkflowExecution, p: Principal, step: bool) -> dict:
    wf = (await db.execute(select(Workflow).where(Workflow.id == ex.workflow_id))).scalar_one()
    graph = await _graph(db, wf, ex.version)
    await engine.drive(db, ex, graph, await _initiator(db, ex, p), step_once=step)
    return to_dict(ex)


@wf_exec_router.post("/{execution_id}/approve", summary="Approve or reject a human-approval step")
async def approve(execution_id: str, body: ApprovalIn, db: AsyncSession = Depends(get_db),
                  p: Principal = Depends(require("workflows:approve"))) -> dict:
    ex = await get_owned(db, WorkflowExecution, execution_id, p.org_id)
    if ex.status != "waiting_approval" or not ex.paused_at_node:
        raise ConflictError("Execution is not waiting for approval")
    if ex.created_by == p.user_id and len(p.roles) and "Platform Administrator" not in p.roles:
        raise ConflictError("Initiators cannot approve their own workflow steps (separation of duties)")
    node = ex.paused_at_node
    ex.state["approvals"][node] = body.decision
    ex.state.setdefault("approvers", {})[node] = {"user": p.email, "comment": body.comment, "at": utcnow().isoformat()}
    await audit.record(db, org_id=p.org_id, action=f"workflow.step_{body.decision}", category="workflow_approval",
                       actor_id=p.user_id, actor_email=p.email, resource_type="workflow_execution", resource_id=ex.id,
                       details={"node": node, "comment": body.comment})
    return await _resume(db, ex, p, step=False)


@wf_exec_router.post("/{execution_id}/resume", summary="Resume from a breakpoint (step=true executes one node)")
async def resume(execution_id: str, body: ResumeIn, db: AsyncSession = Depends(get_db),
                 p: Principal = Depends(require("workflows:execute"))) -> dict:
    ex = await get_owned(db, WorkflowExecution, execution_id, p.org_id)
    if ex.status != "paused":
        raise ConflictError(f"Execution is {ex.status}, not paused")
    ex.state["resume_past"] = ex.paused_at_node
    return await _resume(db, ex, p, step=body.step)


@wf_exec_router.post("/{execution_id}/cancel")
async def cancel(execution_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("workflows:execute"))) -> dict:
    ex = await get_owned(db, WorkflowExecution, execution_id, p.org_id)
    if ex.status not in ("paused", "waiting_approval", "running"):
        raise ConflictError(f"Execution is {ex.status}")
    ex.status, ex.finished_at = "cancelled", utcnow()
    return to_dict(ex)
