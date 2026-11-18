"""Shared API helpers: serialization, pagination, tenant-scoped lookups, idempotency."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, TypeVar

from fastapi import Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.inspection import inspect as sa_inspect

from app.core.errors import NotFoundError
from app.core.redis import kv_get, kv_set

T = TypeVar("T")


def to_dict(obj: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    exclude = exclude or set()
    out: dict[str, Any] = {}
    for attr in sa_inspect(obj).mapper.column_attrs:
        k = attr.key
        if k in exclude:
            continue
        v = getattr(obj, k)
        out[k] = v.isoformat() if isinstance(v, datetime) else v
    return out


class Pagination:
    def __init__(self, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> None:
        self.limit, self.offset = limit, offset


async def paginate(db: AsyncSession, stmt, p: Pagination, ser=to_dict) -> dict[str, Any]:
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await db.execute(stmt.limit(p.limit).offset(p.offset))).scalars().all()
    return {"items": [ser(r) for r in rows], "total": total, "limit": p.limit, "offset": p.offset}


async def get_owned(db: AsyncSession, model: type[T], obj_id: str, org_id: str) -> T:
    """Tenant isolation: objects from other organizations are indistinguishable from missing (404)."""
    obj = (await db.execute(select(model).where(model.id == obj_id, model.organization_id == org_id))).scalar_one_or_none()
    if obj is None:
        raise NotFoundError(f"{model.__name__} not found")
    return obj


def client_meta(request: Request) -> dict[str, str | None]:
    return {"ip": request.client.host if request.client else None, "user_agent": request.headers.get("user-agent")}


async def idempotent_lookup(request: Request, org_id: str) -> tuple[str | None, Any | None]:
    key = request.headers.get("idempotency-key")
    if not key:
        return None, None
    ck = f"idem:{org_id}:{request.url.path}:{key[:100]}"
    cached = await kv_get(ck)
    return ck, (json.loads(cached) if cached else None)


async def idempotent_store(ck: str | None, payload: Any) -> None:
    if ck:
        await kv_set(ck, json.dumps(payload, default=str), 86_400)
