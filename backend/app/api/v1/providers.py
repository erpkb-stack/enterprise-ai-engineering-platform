from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import get_owned, to_dict
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ConflictError
from app.core.ids import new_id
from app.core.resilience import breakers
from app.integrations.llm.base import ChatMessage, CompletionRequest
from app.integrations.llm.gateway import PROVIDER_KINDS, Route, gateway
from app.models import ModelConfiguration, ModelProvider
from app.security import audit
from app.security.crypto import encrypt_secret
from app.security.principal import Principal, require

router = APIRouter(prefix="/providers", tags=["providers"])


class ProviderIn(BaseModel):
    name: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9_\-]+$")
    kind: Literal["mock", "ollama", "openai", "anthropic", "gemini", "nvidia_nim", "openai_compatible"]
    base_url: str | None = Field(None, pattern=r"^https?://")
    api_key: str | None = Field(None, description="Write-only; encrypted at rest")
    is_enabled: bool = True


class ModelConfigIn(BaseModel):
    provider_id: str
    model_name: str = Field(min_length=1, max_length=200)
    display_name: str = ""
    context_window: int = Field(8192, ge=512)
    input_cost_per_1k: float = Field(0.0, ge=0)
    output_cost_per_1k: float = Field(0.0, ge=0)
    supports_tools: bool = False
    is_default: bool = False


class PlaygroundIn(BaseModel):
    provider: str
    model: str
    prompt: str = Field(min_length=1, max_length=20_000)
    system: str = ""
    temperature: float = Field(0.2, ge=0, le=2)
    max_tokens: int = Field(400, ge=1, le=4000)


def _prov(p: ModelProvider) -> dict:
    return {**to_dict(p, exclude={"credential_encrypted"}), "has_api_key": bool(p.credential_encrypted), "builtin": False}


@router.get("", summary="Built-in (env-configured) and organization-registered providers")
async def list_providers(db: AsyncSession = Depends(get_db), p: Principal = Depends(require("providers:read"))) -> dict:
    s = get_settings()
    builtin = []
    for name in gateway.builtin_names():
        configured = {"mock": True, "ollama": True, "openai": bool(s.openai_api_key), "anthropic": bool(s.anthropic_api_key),
                      "gemini": bool(s.gemini_api_key), "nvidia_nim": bool(s.nvidia_nim_api_key)}.get(name, False)
        builtin.append({"name": name, "kind": name, "builtin": True, "configured": configured, "is_mock": name == "mock",
                        "circuit": breakers.get(f"llm:{name}").snapshot()})
    org = (await db.execute(select(ModelProvider).where(ModelProvider.organization_id == p.org_id))).scalars().all()
    models = (await db.execute(select(ModelConfiguration).where(ModelConfiguration.organization_id == p.org_id))).scalars().all()
    return {"builtin": builtin, "organization": [_prov(x) for x in org], "models": [to_dict(m) for m in models],
            "kinds": PROVIDER_KINDS, "default": {"provider": s.default_provider, "model": s.default_model},
            "embedding": {"provider": s.embedding_provider, "model": s.embedding_model, "dim": s.embedding_dim}}


@router.post("", status_code=201)
async def create_provider(body: ProviderIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("providers:manage"))) -> dict:
    if (await db.execute(select(ModelProvider).where(ModelProvider.organization_id == p.org_id, ModelProvider.name == body.name))).scalar_one_or_none():
        raise ConflictError("Provider name already exists")
    row = ModelProvider(id=new_id(), organization_id=p.org_id, name=body.name, kind=body.kind, base_url=body.base_url,
                        credential_encrypted=encrypt_secret(body.api_key), is_enabled=body.is_enabled)
    db.add(row)
    await db.flush()
    await audit.record(db, org_id=p.org_id, action="provider.create", category="agent_config", actor_id=p.user_id,
                       actor_email=p.email, resource_type="model_provider", resource_id=row.id,
                       details={"name": body.name, "kind": body.kind, "has_api_key": bool(body.api_key)})
    return _prov(row)


@router.delete("/{provider_id}")
async def delete_provider(provider_id: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("providers:manage"))) -> dict:
    row = await get_owned(db, ModelProvider, provider_id, p.org_id)
    await audit.record(db, org_id=p.org_id, action="provider.delete", category="agent_config", actor_id=p.user_id,
                       actor_email=p.email, resource_type="model_provider", resource_id=row.id)
    await db.delete(row)
    return {"ok": True}


@router.get("/{name}/health")
async def provider_health(name: str, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("providers:read"))) -> dict:
    prov = await gateway.resolve(db, p.org_id, name)
    return {"provider": name, "kind": prov.kind, "is_mock": prov.is_mock, **(await prov.health_check())}


@router.post("/models", status_code=201)
async def create_model(body: ModelConfigIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("providers:manage"))) -> dict:
    await get_owned(db, ModelProvider, body.provider_id, p.org_id)
    m = ModelConfiguration(organization_id=p.org_id, **body.model_dump())
    db.add(m)
    await db.flush()
    return to_dict(m)


@router.post("/playground", summary="Single completion against any provider (for connectivity checks)")
async def playground(body: PlaygroundIn, db: AsyncSession = Depends(get_db), p: Principal = Depends(require("prompts:test"))) -> dict:
    msgs = ([ChatMessage(role="system", content=body.system)] if body.system else []) + [ChatMessage(role="user", content=body.prompt)]
    resp, meta = await gateway.complete(CompletionRequest(messages=msgs, model=body.model, temperature=body.temperature,
                                                          max_tokens=body.max_tokens),
                                        route=Route(body.provider, body.model), db=db, org_id=p.org_id, purpose="playground")
    return {**resp.model_dump(), **meta}
