"""Request principal resolution (JWT bearer or API key) and permission dependencies."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.ids import utcnow
from app.core.logging import org_id_var
from app.models import ApiKey, User
from app.security.crypto import decode_access_token, sha256_hex

bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    user_id: str
    org_id: str
    email: str
    roles: list[str]
    permissions: set[str]
    team_ids: list[str] = field(default_factory=list)
    auth_method: str = "jwt"
    ip: str | None = None
    user_agent: str | None = None

    def has(self, perm: str) -> bool:
        return perm in self.permissions

    def require(self, perm: str) -> None:
        if not self.has(perm):
            raise PermissionDeniedError(f"Missing permission '{perm}'", details={"permission": perm})


_PRINCIPAL_TTL_S = 15.0
_principal_cache: dict[str, tuple[float, dict]] = {}


def invalidate_principal(user_id: str | None = None) -> None:
    """Called on role/permission/user changes; a full clear when user_id is None."""
    if user_id is None:
        _principal_cache.clear()
    else:
        _principal_cache.pop(user_id, None)


async def _identity(db: AsyncSession, user_id: str) -> dict:
    hit = _principal_cache.get(user_id)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    user = await _load_user(db, user_id)
    ident = {"org_id": user.organization_id, "email": user.email, "roles": user.role_names,
             "permissions": frozenset(user.permission_codes), "team_ids": [t.id for t in user.teams]}
    if len(_principal_cache) > 10_000:
        _principal_cache.clear()
    _principal_cache[user_id] = (now + _PRINCIPAL_TTL_S, ident)
    return ident


async def _load_user(db: AsyncSession, user_id: str) -> User:
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthenticationError("User inactive or not found")
    return user


async def get_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    api_key = request.headers.get("x-api-key")
    if api_key:
        key = (
            await db.execute(select(ApiKey).where(ApiKey.key_hash == sha256_hex(api_key), ApiKey.revoked_at.is_(None)))
        ).scalar_one_or_none()
        if key is None or (key.expires_at and key.expires_at < utcnow()):
            raise AuthenticationError("Invalid API key")
        user = await _load_user(db, key.user_id)
        key.last_used_at = utcnow()
        perms = user.permission_codes
        if key.scopes:  # API keys can only narrow, never widen, the owner's permissions
            perms = perms & set(key.scopes)
        principal = Principal(user.id, user.organization_id, user.email, user.role_names, perms,
                              [t.id for t in user.teams], "api_key", ip, ua)
    else:
        token = creds.credentials if creds else None  # never accept tokens via query string (leaks to logs)
        if not token:
            raise AuthenticationError("Missing credentials")
        claims = decode_access_token(token)
        ident = await _identity(db, claims["sub"])
        if ident["org_id"] != claims["org"]:
            raise AuthenticationError("Token organization mismatch")
        principal = Principal(claims["sub"], ident["org_id"], ident["email"], ident["roles"], set(ident["permissions"]),
                              ident["team_ids"], "jwt", ip, ua)
    org_id_var.set(principal.org_id)
    request.state.principal = principal
    return principal


def require(*perms: str):
    """Dependency factory: `principal: Principal = Depends(require("agents:write"))`."""

    async def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        for p in perms:
            principal.require(p)
        return principal

    return _dep
