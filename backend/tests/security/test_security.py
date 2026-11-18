"""Security tests: authentication, session rotation, RBAC, tenant isolation, secrets exposure, rate limiting,
audit integrity."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from tests.conftest import API, PASSWORD, agent_id, login

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_login_me_and_bad_password(client):
    r = await client.post(f"{API}/auth/login", json={"email": "dev@aiecc.local", "password": "nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"
    r = await client.post(f"{API}/auth/login", json={"email": "dev@aiecc.local", "password": PASSWORD})
    assert r.status_code == 200 and "Developer" in r.json()["user"]["roles"]
    me = await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["email"] == "dev@aiecc.local" and "agents:execute" in me.json()["permissions"]


async def test_missing_and_invalid_tokens_rejected(client):
    assert (await client.get(f"{API}/agents")).status_code == 401
    assert (await client.get(f"{API}/agents", headers={"Authorization": "Bearer abc.def.ghi"})).status_code == 401


async def test_account_lockout_after_repeated_failures(client, admin):
    r = await client.post(f"{API}/security/users", headers=admin, json={
        "email": "lockme@aiecc.local", "password": "Lockout-Test-123", "roles": ["Developer"]})
    assert r.status_code == 201, r.text
    for _ in range(5):
        await client.post(f"{API}/auth/login", json={"email": "lockme@aiecc.local", "password": "wrong"})
    r = await client.post(f"{API}/auth/login", json={"email": "lockme@aiecc.local", "password": "Lockout-Test-123"})
    assert r.status_code == 401 and "locked" in r.json()["error"]["message"]


async def test_refresh_rotation_and_reuse_detection(client):
    r = await client.post(f"{API}/auth/login", json={"email": "viewer@aiecc.local", "password": PASSWORD})
    rt1 = r.json()["refresh_token"]
    r2 = await client.post(f"{API}/auth/refresh", json={"refresh_token": rt1})
    assert r2.status_code == 200
    rt2 = r2.json()["refresh_token"]
    assert rt2 != rt1
    replay = await client.post(f"{API}/auth/refresh", json={"refresh_token": rt1})
    assert replay.status_code == 401 and "reuse" in replay.json()["error"]["message"]
    # the whole family (including the newest token) is now revoked
    assert (await client.post(f"{API}/auth/refresh", json={"refresh_token": rt2})).status_code == 401


async def test_weak_password_rejected(client, admin):
    r = await client.post(f"{API}/security/users", headers=admin, json={"email": "weak@aiecc.local", "password": "aaaaaaaaaaaa"})
    assert r.status_code == 422


@pytest.mark.parametrize("who,method,path,body,expected", [
    ("viewer", "post", "/agents", {"name": "x-agent", "agent_type": "custom"}, 403),
    ("dev", "post", "/mcp/servers", {"name": "x", "transport": "stdio", "command": "python"}, 403),
    ("dev", "get", "/audit/events", None, 403),
    ("auditor", "get", "/audit/events", None, 200),
    ("auditor", "post", "/knowledge/search", {"query": "x"}, 200),
    ("viewer", "post", "/redteam/run", {}, 403),
    ("dev", "get", "/security/users", None, 403),
    ("aieng", "put", "/settings/feature-flags", {"flags": {"llm_judge": True}}, 403),
])
async def test_rbac_matrix(client, who, method, path, body, expected):
    h = await login(client, f"{who}@aiecc.local")
    r = await getattr(client, method)(f"{API}{path}", headers=h, **({"json": body} if body is not None else {}))
    assert r.status_code == expected, r.text


async def test_tenant_isolation_resources_return_404(client, admin, globex):
    aid = await agent_id(client, admin, "Enterprise Research Agent")
    assert (await client.get(f"{API}/agents/{aid}", headers=globex)).status_code == 404
    assert (await client.post(f"{API}/agents/{aid}/execute", headers=globex, json={"input": "hi"})).status_code == 404
    names = [a["name"] for a in (await client.get(f"{API}/agents", headers=globex)).json()["items"]]
    assert "Enterprise Research Agent" not in names


async def test_tenant_isolation_knowledge_search(client, admin, globex):
    r = await client.post(f"{API}/knowledge/search", headers=admin, json={"query": "Project Hydra confidential roadmap",
                                                                           "strategy": "hybrid", "top_k": 10})
    assert all("globex" not in h["filename"] for h in r.json()["hits"])
    docs = (await client.get(f"{API}/knowledge/documents", headers=globex)).json()["items"]
    gid = docs[0]["id"]
    r = await client.post(f"{API}/knowledge/search", headers=admin, json={"query": "Hydra", "document_ids": [gid]})
    assert r.json()["hits"] == []
    assert (await client.get(f"{API}/knowledge/documents/{gid}/chunks", headers=admin)).status_code == 404


async def test_secrets_never_returned(client, admin):
    r = await client.post(f"{API}/mcp/servers", headers=admin, json={
        "name": "secret-http", "transport": "streamable_http", "url": "http://127.0.0.1:9/mcp", "auth_type": "api_key",
        "credentials": {"api_key": "sk-test-SHOULD-NOT-LEAK-123456", "header": "X-API-Key"}})
    assert r.status_code == 201
    body = r.text + (await client.get(f"{API}/mcp/servers/{r.json()['id']}", headers=admin)).text
    assert "SHOULD-NOT-LEAK" not in body and "credential_encrypted" not in body
    assert r.json()["has_credentials"] is True
    r = await client.post(f"{API}/providers", headers=admin, json={"name": "openai-x", "kind": "openai",
                                                                  "api_key": "sk-provider-NOLEAK-99999999"})
    assert r.status_code == 201 and "NOLEAK" not in r.text and r.json()["has_api_key"]
    audit = (await client.get(f"{API}/audit/events", headers=admin, params={"limit": 200})).text
    assert "SHOULD-NOT-LEAK" not in audit and "NOLEAK" not in audit


async def test_api_key_scopes_cannot_exceed_owner(client, dev, admin):
    r = await client.post(f"{API}/security/api-keys", headers=dev, json={"name": "too-wide", "scopes": ["security:manage"]})
    assert r.status_code == 403
    r = await client.post(f"{API}/security/api-keys", headers=dev, json={"name": "narrow", "scopes": ["agents:read"]})
    key = r.json()["key"]
    assert (await client.get(f"{API}/agents", headers={"X-API-Key": key})).status_code == 200
    aid = await agent_id(client, admin, "Coding Agent")
    r = await client.post(f"{API}/agents/{aid}/execute", headers={"X-API-Key": key}, json={"input": "hi"})
    assert r.status_code == 403, "key scope narrower than owner's permissions"


async def test_security_headers_present(client):
    r = await client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers.get("x-request-id")


async def test_rate_limiting_returns_429(client, monkeypatch, app):
    from app.api.middleware import RequestContextMiddleware

    mw = app.middleware_stack
    while mw is not None and not isinstance(mw, RequestContextMiddleware):
        mw = getattr(mw, "app", None)
    assert mw is not None
    monkeypatch.setattr(mw.settings, "rate_limit_auth_per_minute", 2)
    codes = [(await client.post(f"{API}/auth/login", json={"email": "nobody@aiecc.local", "password": "x"})).status_code
             for _ in range(4)]
    assert 429 in codes
    r = await client.post(f"{API}/auth/login", json={"email": "nobody@aiecc.local", "password": "x"})
    assert r.headers.get("retry-after")


async def test_audit_chain_verifies_and_detects_tampering(client, admin, auditor):
    r = await client.get(f"{API}/audit/verify", headers=auditor)
    assert r.status_code == 200 and r.json()["valid"] is True and r.json()["checked"] > 5
    from app.core.db import get_sessionmaker
    from app.models import AuditEvent

    async with get_sessionmaker()() as db:
        if db.bind.dialect.name == "postgresql":
            with pytest.raises(Exception, match="append-only"):
                await db.execute(text("UPDATE audit_events SET action='tampered'"))
            return
        ev = (await db.execute(select(AuditEvent).order_by(AuditEvent.chain_index).limit(1))).scalars().first()
        org = ev.organization_id
        first = (await db.execute(select(AuditEvent).where(AuditEvent.organization_id == org)
                                  .order_by(AuditEvent.chain_index).limit(1))).scalar_one()
        original = first.action
        first.action = "tampered.action"
        await db.commit()
        from app.security.audit import verify_chain

        res = await verify_chain(db, org)
        assert res["valid"] is False and "tampered" in res["reason"]
        first.action = original
        await db.commit()
        assert (await verify_chain(db, org))["valid"] is True


@pytest.mark.postgres
async def test_postgres_rejects_audit_delete(client):
    from app.core.db import get_sessionmaker

    async with get_sessionmaker()() as db:
        with pytest.raises(Exception, match="append-only"):
            await db.execute(text("DELETE FROM audit_events"))
