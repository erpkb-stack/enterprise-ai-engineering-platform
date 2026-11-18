from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import client_meta
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AuthenticationError, NotFoundError
from app.core.ids import new_id, utcnow
from app.domain.types import EmailStr
from app.models import Organization, RefreshToken, User
from app.security import audit
from app.security.crypto import create_access_token, generate_token, sha256_hex, verify_password_async
from app.security.principal import Principal, get_principal

router = APIRouter(prefix="/auth", tags=["auth"])
MAX_FAILED = 5
LOCKOUT = timedelta(minutes=15)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


async def _issue(db: AsyncSession, user: User, request: Request, family: str | None = None) -> dict:
    s = get_settings()
    raw = generate_token()
    rt = RefreshToken(id=new_id(), user_id=user.id, family_id=family or new_id(), token_hash=sha256_hex(raw),
                      expires_at=utcnow() + timedelta(seconds=s.refresh_token_ttl_s),
                      user_agent=(request.headers.get("user-agent") or "")[:300],
                      ip=request.client.host if request.client else None)
    db.add(rt)
    await db.flush()
    return {"access_token": create_access_token(user_id=user.id, org_id=user.organization_id, roles=user.role_names),
            "refresh_token": raw, "token_type": "bearer", "expires_in": s.access_token_ttl_s, "session_id": rt.id}, rt


def _me(user: User, org: Organization | None) -> dict:
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "organization_id": user.organization_id,
            "organization": {"id": org.id, "name": org.name, "slug": org.slug, "is_demo": org.is_demo} if org else None,
            "roles": user.role_names, "permissions": sorted(user.permission_codes),
            "teams": [{"id": t.id, "name": t.name} for t in user.teams]}


@router.post("/login", summary="Authenticate with email/password and receive access + refresh tokens")
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    meta = client_meta(request)
    user = (await db.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    now = utcnow()
    if user is None:
        await verify_password_async(body.password, "$argon2id$v=19$m=19456,t=2,p=1$c29tZXNhbHQ$ZmFrZWhhc2g")  # equalize timing
        await audit.record(db, org_id=None, action="auth.login", category="authentication", actor_email=body.email,
                           outcome="failure", severity="warning", details={"reason": "unknown user"}, **meta)
        await db.commit()
        raise AuthenticationError("Invalid email or password")
    locked_until = user.locked_until
    if locked_until is not None and locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=now.tzinfo)
    if locked_until and locked_until > now:
        await audit.record(db, org_id=user.organization_id, action="auth.login", category="authentication",
                           actor_id=user.id, actor_email=user.email, outcome="denied", severity="warning",
                           details={"reason": "account locked"}, **meta)
        await db.commit()
        raise AuthenticationError("Account temporarily locked due to repeated failures")
    if not user.is_active or not await verify_password_async(body.password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED:
            user.locked_until = now + LOCKOUT
            user.failed_logins = 0
        await audit.record(db, org_id=user.organization_id, action="auth.login", category="authentication",
                           actor_id=user.id, actor_email=user.email, outcome="failure", severity="warning",
                           details={"reason": "bad credentials" if user.is_active else "inactive"}, **meta)
        await db.commit()
        raise AuthenticationError("Invalid email or password")
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
    tokens, rt = await _issue(db, user, request)
    await audit.record(db, org_id=user.organization_id, action="auth.login", category="authentication",
                       actor_id=user.id, actor_email=user.email, details={"session_id": rt.id}, **meta)
    org = (await db.execute(select(Organization).where(Organization.id == user.organization_id))).scalar_one_or_none()
    return {**tokens, "user": _me(user, org)}


@router.post("/refresh", summary="Rotate refresh token (reuse of a rotated token revokes the session family)")
async def refresh(body: RefreshRequest, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    rt = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(body.refresh_token)))).scalar_one_or_none()
    if rt is None:
        raise AuthenticationError("Invalid refresh token")
    now = utcnow()
    exp = rt.expires_at if rt.expires_at.tzinfo else rt.expires_at.replace(tzinfo=now.tzinfo)
    user = (await db.execute(select(User).where(User.id == rt.user_id))).scalar_one()
    if rt.revoked_at is not None:
        # Reuse detected: an attacker or client replayed a rotated token -> revoke the entire family.
        fam = (await db.execute(select(RefreshToken).where(RefreshToken.family_id == rt.family_id,
                                                           RefreshToken.revoked_at.is_(None)))).scalars().all()
        for f in fam:
            f.revoked_at = now
        await audit.record(db, org_id=user.organization_id, action="auth.refresh_reuse_detected", category="authentication",
                           actor_id=user.id, actor_email=user.email, outcome="denied", severity="critical",
                           details={"family_id": rt.family_id, "revoked": len(fam)}, **client_meta(request))
        await db.commit()
        raise AuthenticationError("Refresh token reuse detected; session revoked")
    if exp < now or not user.is_active:
        raise AuthenticationError("Refresh token expired")
    tokens, new_rt = await _issue(db, user, request, family=rt.family_id)
    rt.revoked_at, rt.replaced_by = now, new_rt.id
    await audit.record(db, org_id=user.organization_id, action="auth.refresh", category="authentication",
                       actor_id=user.id, actor_email=user.email, details={"session_id": new_rt.id})
    return tokens


@router.post("/logout")
async def logout(body: RefreshRequest, request: Request, db: AsyncSession = Depends(get_db),
                 p: Principal = Depends(get_principal)) -> dict:
    rt = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(body.refresh_token),
                                                      RefreshToken.user_id == p.user_id))).scalar_one_or_none()
    if rt:
        fam = (await db.execute(select(RefreshToken).where(RefreshToken.family_id == rt.family_id))).scalars().all()
        for f in fam:
            f.revoked_at = f.revoked_at or utcnow()
    await audit.record(db, org_id=p.org_id, action="auth.logout", category="authentication", actor_id=p.user_id,
                       actor_email=p.email, **client_meta(request))
    return {"ok": True}


@router.get("/me")
async def me(p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> dict:
    user = (await db.execute(select(User).where(User.id == p.user_id))).scalar_one()
    org = (await db.execute(select(Organization).where(Organization.id == user.organization_id))).scalar_one_or_none()
    return {**_me(user, org), "auth_method": p.auth_method}


@router.get("/sessions", summary="Active sessions (refresh-token families) for the current user")
async def sessions(p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(RefreshToken).where(RefreshToken.user_id == p.user_id, RefreshToken.revoked_at.is_(None))
                             .order_by(RefreshToken.created_at.desc()))).scalars().all()
    return [{"id": r.id, "family_id": r.family_id, "created_at": r.created_at.isoformat(), "expires_at": r.expires_at.isoformat(),
             "ip": r.ip, "user_agent": r.user_agent} for r in rows]


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: str, p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> dict:
    rt = (await db.execute(select(RefreshToken).where(RefreshToken.id == session_id, RefreshToken.user_id == p.user_id))).scalar_one_or_none()
    if rt is None:
        raise NotFoundError("Session not found")
    fam = (await db.execute(select(RefreshToken).where(RefreshToken.family_id == rt.family_id))).scalars().all()
    for f in fam:
        f.revoked_at = f.revoked_at or utcnow()
    await audit.record(db, org_id=p.org_id, action="auth.session_revoked", category="authentication",
                       actor_id=p.user_id, actor_email=p.email, details={"session_id": session_id})
    return {"ok": True}
