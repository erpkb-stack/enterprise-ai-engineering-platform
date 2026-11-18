"""Append-only, tamper-evident audit log.

Each event stores `prev_hash` and `hash = SHA256(prev_hash || canonical_json(event))` per organization,
forming a hash chain. Any modification or deletion of a historical row breaks verification.
On PostgreSQL a trigger additionally rejects UPDATE/DELETE (see migration 0001).

Secrets and raw prompt bodies are never written: `details` passes through `scrub()`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id, utcnow
from app.core.logging import get_logger, redact, request_id_var
from app.models import AuditEvent

log = get_logger(__name__)

GENESIS = "0" * 64
_SENSITIVE_KEYS = {"password", "secret", "token", "api_key", "apikey", "credential", "credentials", "authorization",
                   "refresh_token", "access_token", "client_secret", "private_key"}
_PROMPT_KEYS = {"system", "developer", "user_template", "prompt", "messages"}
_lock = asyncio.Lock()


def scrub(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "<truncated>"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            kl = str(k).lower()
            if any(s in kl for s in _SENSITIVE_KEYS):
                out[k] = "***redacted***"
            elif kl in _PROMPT_KEYS and isinstance(v, str):
                out[k] = {"sha256": hashlib.sha256(v.encode()).hexdigest()[:16], "chars": len(v)}
            else:
                out[k] = scrub(v, depth + 1)
        return out
    if isinstance(value, list):
        return [scrub(v, depth + 1) for v in value[:50]]
    if isinstance(value, str):
        v = redact(value)
        return v if len(v) <= 500 else v[:500] + "…"
    return value


def _canonical(ev: dict[str, Any]) -> str:
    return json.dumps(ev, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(prev_hash: str, fields: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical(fields)).encode()).hexdigest()


def _hash_fields(e: AuditEvent | dict[str, Any]) -> dict[str, Any]:
    get = (lambda k: e[k]) if isinstance(e, dict) else (lambda k: getattr(e, k))
    created = get("created_at")
    if isinstance(created, datetime):
        created = created.replace(tzinfo=None).isoformat(timespec="microseconds")
    return {
        "id": get("id"),
        "organization_id": get("organization_id"),
        "chain_index": get("chain_index"),
        "created_at": created,
        "actor_id": get("actor_id"),
        "action": get("action"),
        "category": get("category"),
        "resource_type": get("resource_type"),
        "resource_id": get("resource_id"),
        "outcome": get("outcome"),
        "details": get("details"),
    }


async def record(
    db: AsyncSession,
    *,
    org_id: str | None,
    action: str,
    category: str,
    actor_id: str | None = None,
    actor_email: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    outcome: str = "success",
    severity: str = "info",
    details: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuditEvent:
    """Append an audit event within the caller's transaction (atomic with the audited change)."""
    async with _lock:
        for attempt in range(5):
            last = (
                await db.execute(
                    select(AuditEvent.chain_index, AuditEvent.hash)
                    .where(AuditEvent.organization_id.is_(None) if org_id is None else AuditEvent.organization_id == org_id)
                    .order_by(AuditEvent.chain_index.desc())
                    .limit(1)
                )
            ).first()
            idx = (last[0] + 1) if last else 0
            prev = last[1] if last else GENESIS
            ev = AuditEvent(
                id=new_id(),
                organization_id=org_id,
                chain_index=idx,
                created_at=utcnow(),
                actor_id=actor_id,
                actor_email=actor_email,
                action=action,
                category=category,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome,
                severity=severity,
                ip=ip,
                user_agent=(user_agent or "")[:300] or None,
                request_id=request_id_var.get(),
                details=scrub(details or {}),
                prev_hash=prev,
            )
            ev.hash = compute_hash(prev, _hash_fields(ev))
            try:
                async with db.begin_nested():
                    db.add(ev)
                    await db.flush()
                return ev
            except IntegrityError:
                log.warning("audit_chain_conflict_retry attempt=%s", attempt)
        raise RuntimeError("could not append audit event after retries")


async def verify_chain(db: AsyncSession, org_id: str, limit: int = 100_000) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(AuditEvent).where(AuditEvent.organization_id == org_id).order_by(AuditEvent.chain_index).limit(limit)
        )
    ).scalars().all()
    prev = GENESIS
    for i, ev in enumerate(rows):
        if ev.chain_index != i:
            return {"valid": False, "checked": i, "broken_at": ev.chain_index, "reason": "gap in chain index"}
        if ev.prev_hash != prev:
            return {"valid": False, "checked": i, "broken_at": ev.chain_index, "reason": "prev_hash mismatch"}
        expected = compute_hash(prev, _hash_fields(ev))
        if expected != ev.hash:
            return {"valid": False, "checked": i, "broken_at": ev.chain_index, "reason": "content hash mismatch (tampered)"}
        prev = ev.hash
    total = (await db.execute(select(func.count()).select_from(AuditEvent).where(AuditEvent.organization_id == org_id))).scalar_one()
    return {"valid": True, "checked": len(rows), "total": total, "head_hash": prev}
