"""Users, roles, teams, API keys, audit trail."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import Pagination, get_owned, paginate, to_dict
from app.core.db import get_db
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationFailedError
from app.core.ids import new_id, utcnow
from app.domain.types import EmailStr
from app.models import ApiKey, AuditEvent, Role, Team, User
from app.security import audit
from app.security.crypto import generate_token, hash_password_async, sha256_hex, validate_password_strength
from app.security.principal import Principal, invalidate_principal, require
from app.security.rbac import PERMISSIONS

router = APIRouter(tags=["security"])


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field("", max_length=200)
    password: str = Field(min_length=12, max_length=200)
    roles: list[str] = Field(default_factory=lambda: ["Developer"])


class UserRolesUpdate(BaseModel):
    roles: list[str]
    is_active: bool | None = None


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


class TeamCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str = ""
    member_ids: list[str] = Field(default_factory=list)


def _user(u: User) -> dict:
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "is_active": u.is_active, "roles": u.role_names,
            "teams": [t.name for t in u.teams], "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "created_at": u.created_at.isoformat()}


@router.get("/security/roles")
async def list_roles(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("agents:read"))) -> list[dict]:
    roles = (await db.execute(select(Role).order_by(Role.name))).scalars().all()
    return [{"id": r.id, "name": r.name, "description": r.description, "permissions": sorted(x.code for x in r.permissions)}
            for r in roles]


@router.get("/security/permissions")
async def list_permissions(p: Principal = Depends(require("agents:read"))) -> dict[str, str]:
    return PERMISSIONS


@router.get("/security/users")
async def list_users(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("security:manage"))) -> list[dict]:
    users = (await db.execute(select(User).where(User.organization_id == p.org_id).order_by(User.email))).scalars().all()
    return [_user(u) for u in users]


@router.post("/security/users", status_code=201)
async def create_user(body: UserCreate, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("security:manage"))) -> dict:
    if problems := validate_password_strength(body.password):
        raise ValidationFailedError("Weak password", details={"problems": problems})
    if (await db.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none():
        raise ConflictError("Email already registered")
    roles = (await db.execute(select(Role).where(Role.name.in_(body.roles)))).scalars().all()
    if len(roles) != len(set(body.roles)):
        raise ValidationFailedError("Unknown role(s)")
    u = User(id=new_id(), organization_id=p.org_id, email=body.email.lower(), full_name=body.full_name,
             password_hash=await hash_password_async(body.password), roles=list(roles), teams=[])
    db.add(u)
    await db.flush()
    await audit.record(db, org_id=p.org_id, action="user.create", category="permission_change", actor_id=p.user_id,
                       actor_email=p.email, resource_type="user", resource_id=u.id, details={"roles": body.roles})
    return _user(u)


@router.put("/security/users/{user_id}")
async def update_user_roles(user_id: str, body: UserRolesUpdate, db: AsyncSession = Depends(get_db),
                            p: Principal = Depends(require("security:manage"))) -> dict:
    u = await get_owned(db, User, user_id, p.org_id)
    roles = (await db.execute(select(Role).where(Role.name.in_(body.roles)))).scalars().all()
    if len(roles) != len(set(body.roles)):
        raise ValidationFailedError("Unknown role(s)")
    if u.id == p.user_id and "Platform Administrator" not in body.roles:
        raise PermissionDeniedError("You cannot remove your own administrator role")
    before = u.role_names
    u.roles = list(roles)
    if body.is_active is not None:
        u.is_active = body.is_active
    invalidate_principal(u.id)
    await audit.record(db, org_id=p.org_id, action="user.roles_changed", category="permission_change",
                       actor_id=p.user_id, actor_email=p.email, resource_type="user", resource_id=u.id,
                       severity="warning", details={"before": before, "after": body.roles, "is_active": u.is_active})
    return _user(u)


@router.get("/security/teams")
async def list_teams(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("agents:read"))) -> list[dict]:
    teams = (await db.execute(select(Team).where(Team.organization_id == p.org_id))).scalars().all()
    out = []
    for t in teams:
        await db.refresh(t, ["members"])
        out.append({"id": t.id, "name": t.name, "description": t.description, "members": [m.email for m in t.members]})
    return out


@router.post("/security/teams", status_code=201)
async def create_team(body: TeamCreate, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("security:manage"))) -> dict:
    members = (await db.execute(select(User).where(User.organization_id == p.org_id, User.id.in_(body.member_ids)))).scalars().all()
    t = Team(id=new_id(), organization_id=p.org_id, name=body.name, description=body.description, members=list(members))
    db.add(t)
    await db.flush()
    await audit.record(db, org_id=p.org_id, action="team.create", category="permission_change", actor_id=p.user_id,
                       actor_email=p.email, resource_type="team", resource_id=t.id)
    return {"id": t.id, "name": t.name}


@router.get("/security/api-keys")
async def list_keys(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("agents:read"))) -> list[dict]:
    q = select(ApiKey).where(ApiKey.organization_id == p.org_id)
    if not p.has("security:manage"):
        q = q.where(ApiKey.user_id == p.user_id)
    rows = (await db.execute(q.order_by(ApiKey.created_at.desc()))).scalars().all()
    return [to_dict(k, exclude={"key_hash"}) for k in rows]


@router.post("/security/api-keys", status_code=201, summary="Create an API key (shown once; stored as SHA-256 hash)")
async def create_key(body: ApiKeyCreate, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("agents:read"))) -> dict:
    bad = set(body.scopes) - set(PERMISSIONS)
    if bad:
        raise ValidationFailedError("Unknown scopes", details={"scopes": sorted(bad)})
    if not set(body.scopes) <= p.permissions and body.scopes:
        raise PermissionDeniedError("API key scopes cannot exceed your own permissions")
    raw = "aiecc_" + generate_token(24)
    k = ApiKey(id=new_id(), organization_id=p.org_id, user_id=p.user_id, name=body.name, prefix=raw[:12],
               key_hash=sha256_hex(raw), scopes=body.scopes, expires_at=body.expires_at)
    db.add(k)
    await db.flush()
    await audit.record(db, org_id=p.org_id, action="api_key.create", category="permission_change", actor_id=p.user_id,
                       actor_email=p.email, resource_type="api_key", resource_id=k.id, details={"scopes": body.scopes})
    return {**to_dict(k, exclude={"key_hash"}), "key": raw, "warning": "Store this key now; it cannot be retrieved again."}


@router.delete("/security/api-keys/{key_id}")
async def revoke_key(key_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("agents:read"))) -> dict:
    k = await get_owned(db, ApiKey, key_id, p.org_id)
    if k.user_id != p.user_id and not p.has("security:manage"):
        raise NotFoundError("ApiKey not found")
    k.revoked_at = utcnow()
    await audit.record(db, org_id=p.org_id, action="api_key.revoke", category="permission_change", actor_id=p.user_id,
                       actor_email=p.email, resource_type="api_key", resource_id=k.id)
    return {"ok": True}


@router.get("/audit/events", summary="Query the append-only audit trail")
async def audit_events(category: str | None = None, action: str | None = None, outcome: str | None = None,
                       actor: str | None = None, severity: str | None = None, since: datetime | None = None,
                       page: Pagination = Depends(), db: AsyncSession = Depends(get_db),
                       p: Principal = Depends(require("audit:read"))) -> dict:
    q = select(AuditEvent).where(AuditEvent.organization_id == p.org_id)
    if category:
        q = q.where(AuditEvent.category == category)
    if action:
        q = q.where(AuditEvent.action.like(f"{action}%"))
    if outcome:
        q = q.where(AuditEvent.outcome == outcome)
    if severity:
        q = q.where(AuditEvent.severity == severity)
    if actor:
        q = q.where(AuditEvent.actor_email.ilike(f"%{actor}%"))
    if since:
        q = q.where(AuditEvent.created_at >= since)
    return await paginate(db, q.order_by(AuditEvent.chain_index.desc()), page)


@router.get("/audit/verify", summary="Verify hash-chain integrity of this organization's audit log")
async def audit_verify(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("audit:verify"))) -> dict:
    res = await audit.verify_chain(db, p.org_id)
    await audit.record(db, org_id=p.org_id, action="audit.verify", category="audit", actor_id=p.user_id,
                       actor_email=p.email, outcome="success" if res["valid"] else "failure",
                       severity="info" if res["valid"] else "critical", details=res)
    return res


@router.get("/audit/summary")
async def audit_summary(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("audit:read")),
                        hours: int = Query(24, ge=1, le=720)) -> dict:
    from datetime import timedelta

    since = utcnow() - timedelta(hours=hours)
    rows = (await db.execute(select(AuditEvent.category, AuditEvent.outcome, func.count()).where(
        AuditEvent.organization_id == p.org_id, AuditEvent.created_at >= since).group_by(AuditEvent.category, AuditEvent.outcome))).all()
    return {"window_hours": hours, "counts": [{"category": c, "outcome": o, "count": n} for c, o, n in rows]}
