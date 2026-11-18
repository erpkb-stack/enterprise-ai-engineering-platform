"""Integration: agent lifecycle/execution, bounded autonomy, cancellation, idempotency, MCP STDIO + HTTP, tools."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import API, agent_id, wait_for

pytestmark = pytest.mark.asyncio(loop_scope="session")
DEMO = Path(__file__).resolve().parents[2] / "app" / "integrations" / "mcp" / "demo_server.py"


async def test_flagship_research_scenario(client, aieng, admin):
    """Orchestrator -> research agent -> RAG + MCP tool -> grounded answer -> citation verification -> audit."""
    orch = await agent_id(client, admin, "Orchestrator Agent")
    r = await client.post(f"{API}/agents/{orch}/execute", headers=aieng,
                          json={"input": "How quickly must a Sev2 incident be acknowledged, and what is the checkout service status?"})
    assert r.status_code == 200, r.text
    ex = r.json()
    assert ex["status"] == "succeeded" and ex["is_mock"] is True
    route = ex["steps"][0]
    assert route["type"] == "route" and route["selected"] == "Enterprise Research Agent"
    child = (await client.get(f"{API}/executions/{ex['steps'][1]['child_execution_id']}", headers=aieng)).json()
    tools_used = [s.get("tool") for s in child["steps"] if s.get("action") == "tool"]
    assert "knowledge_search" in tools_used and any(t and t.startswith("aiecc-demo-ops/") for t in tools_used)
    g = child["evaluation"]["grounding"]
    assert g, [s.get("observation", "")[:300] for s in child["steps"]]
    assert g["valid_citations"] >= 1 and g["citation_precision"] == 1.0
    assert "15 minutes" in child["output"]
    assert any(c["filename"] == "incident-response-policy.md" for c in child["citations"])
    events = (await client.get(f"{API}/audit/events", headers=admin, params={"action": "agent.execute", "limit": 5})).json()
    assert events["total"] >= 2
    tool_events = (await client.get(f"{API}/audit/events", headers=admin, params={"action": "tool.execute"})).json()
    assert tool_events["total"] >= 2


async def test_agent_lifecycle_versioning_and_clone(client, aieng):
    r = await client.post(f"{API}/agents", headers=aieng, json={
        "name": "Lifecycle Test Agent", "agent_type": "custom",
        "config": {"system_prompt": "v1", "tools": ["calculator"], "max_steps": 3}})
    assert r.status_code == 201 and r.json()["status"] == "draft"
    aid = r.json()["id"]
    assert (await client.post(f"{API}/agents/{aid}/execute", headers=aieng, json={"input": "2+2"})).status_code == 409
    r = await client.put(f"{API}/agents/{aid}", headers=aieng, json={"config": {"system_prompt": "v2", "tools": ["calculator"],
                                                                                "max_steps": 3}, "change_note": "tweak"})
    assert r.json()["current_version"] == 2
    vs = (await client.get(f"{API}/agents/{aid}/versions", headers=aieng)).json()
    assert [v["version"] for v in vs] == [2, 1]
    assert (await client.post(f"{API}/agents/{aid}/lifecycle", headers=aieng, json={"status": "active"})).status_code == 200
    r = await client.post(f"{API}/agents/{aid}/execute", headers=aieng, json={"input": "Calculate 12 * 7"})
    ex = r.json()
    assert ex["status"] == "succeeded" and "84" in ex["output"]
    c = await client.post(f"{API}/agents/{aid}/clone", headers=aieng, json={"name": "Lifecycle Clone"})
    assert c.status_code == 201 and c.json()["config"]["system_prompt"] == "v2"
    assert (await client.post(f"{API}/agents/{aid}/lifecycle", headers=aieng, json={"status": "archived"})).status_code == 200
    r = await client.post(f"{API}/agents/{aid}/lifecycle", headers=aieng, json={"status": "active"})
    assert r.status_code == 409, "archived is terminal"


async def test_max_steps_hard_limit_validated(client, aieng):
    r = await client.post(f"{API}/agents", headers=aieng, json={"name": "Unbounded", "agent_type": "custom",
                                                                "config": {"max_steps": 1000}})
    assert r.status_code == 422


async def test_agent_cannot_call_tool_outside_scope_or_high_risk(client, aieng):
    r = await client.post(f"{API}/agents", headers=aieng, json={
        "name": "Scope Agent", "agent_type": "custom",
        "config": {"system_prompt": "x", "tools": ["calculator", "aiecc-demo-ops/drop_database"], "max_steps": 2}})
    aid = r.json()["id"]
    await client.post(f"{API}/agents/{aid}/lifecycle", headers=aieng, json={"status": "active"})
    # call the tool service directly with agent context to assert enforcement independent of the model
    from app.core.db import get_sessionmaker
    from app.security.principal import Principal
    from app.services.tool_service import invoke_tool

    me = (await client.get(f"{API}/auth/me", headers=aieng)).json()
    p = Principal(me["id"], me["organization_id"], me["email"], me["roles"], set(me["permissions"]))
    async with get_sessionmaker()() as db:
        res, d = await invoke_tool(db, org_id=p.org_id, principal=p, qualified="aiecc-demo-ops/drop_database",
                                   arguments={"name": "prod"}, context="agent", agent_tools=["aiecc-demo-ops/drop_database"])
        assert not d.allowed and d.rule == "tool_allowlisted"
        res, d = await invoke_tool(db, org_id=p.org_id, principal=p, qualified="aiecc-demo-ops/get_service_status",
                                   arguments={"service": "auth"}, context="agent", agent_tools=["calculator"])
        assert not d.allowed and d.rule == "agent_tool_scope"
        await db.commit()
    denied = (await client.get(f"{API}/tools/executions", headers=aieng, params={"status": "denied"})).json()
    assert denied["total"] >= 2


async def test_idempotency_key_replays_response(client, dev, admin):
    aid = await agent_id(client, admin, "Coding Agent")
    h = {**dev, "Idempotency-Key": "idem-123"}
    r1 = await client.post(f"{API}/agents/{aid}/execute", headers=h, json={"input": "Explain asyncio.gather"})
    r2 = await client.post(f"{API}/agents/{aid}/execute", headers=h, json={"input": "Explain asyncio.gather"})
    assert r1.json()["id"] == r2.json()["id"] and r2.headers.get("idempotent-replay") == "true"


async def test_timeout_and_cancellation(client, aieng, monkeypatch):
    from app.integrations.llm.mock import MockProvider

    orig = MockProvider.complete

    async def slow(self, req):
        await asyncio.sleep(8)
        return await orig(self, req)

    monkeypatch.setattr(MockProvider, "complete", slow)
    r = await client.post(f"{API}/agents", headers=aieng, json={"name": "Slow Agent", "agent_type": "custom",
                                                                "config": {"total_timeout_s": 5, "timeout_s": 30,
                                                                           "retry": {"max_attempts": 1}}})
    aid = r.json()["id"]
    await client.post(f"{API}/agents/{aid}/lifecycle", headers=aieng, json={"status": "active"})
    ex = (await client.post(f"{API}/agents/{aid}/execute", headers=aieng, json={"input": "hi"})).json()
    assert ex["status"] == "timeout"
    r = await client.post(f"{API}/agents/{aid}/execute", headers=aieng, json={"input": "hi", "mode": "async"})
    assert r.status_code == 202
    eid = r.json()["id"]
    await wait_for(lambda: _status_is(client, aieng, eid, "running"))
    c = await client.post(f"{API}/executions/{eid}/cancel", headers=aieng)
    assert c.status_code == 200
    final = (await client.get(f"{API}/executions/{eid}", headers=aieng)).json()
    assert final["status"] == "cancelled"


async def _status_is(client, h, eid, status):
    return (await client.get(f"{API}/executions/{eid}", headers=h)).json()["status"] == status


async def test_backpressure_rejects_when_saturated(client, dev, admin, monkeypatch):
    from app.agents import runtime
    from app.core.resilience import BoundedExecutor

    monkeypatch.setattr(runtime, "_executor", BoundedExecutor(0, 0))
    aid = await agent_id(client, admin, "Coding Agent")
    r = await client.post(f"{API}/agents/{aid}/execute", headers=dev, json={"input": "x"})
    assert r.status_code == 503 and r.headers.get("retry-after")


async def test_mcp_stdio_register_connect_discover_and_allowlist(client, admin, security, dev):
    r = await client.post(f"{API}/mcp/servers", headers=admin, json={
        "name": "stdio-test", "transport": "stdio", "command": "python", "args": [str(DEMO)]})
    assert r.status_code == 201
    sid = r.json()["id"]
    c = await client.post(f"{API}/mcp/servers/{sid}/connect", headers=admin)
    assert c.status_code == 200, c.text
    names = {t["name"] for t in c.json()["tools"]}
    assert {"get_service_status", "drop_database", "fetch_untrusted_page"} <= names
    assert any(res["uri"] == "runbook://checkout-latency" for res in c.json()["resources"])
    tools = (await client.get(f"{API}/mcp/servers/{sid}/tools", headers=admin)).json()
    status_tool = next(t for t in tools if t["name"] == "get_service_status")
    drop = next(t for t in tools if t["name"] == "drop_database")
    assert status_tool["is_enabled"] is False, "deny-by-default on discovery"
    assert drop["risk_level"] == "high" and drop["requires_approval"]
    r = await client.post(f"{API}/tools/{status_tool['id']}/execute", headers=dev, json={"arguments": {"service": "auth"}})
    assert r.json()["policy"]["allowed"] is False
    assert (await client.put(f"{API}/tools/{status_tool['id']}", headers=dev, json={"is_enabled": True})).status_code == 403
    assert (await client.put(f"{API}/tools/{status_tool['id']}", headers=security, json={"is_enabled": True})).status_code == 200
    r = await client.post(f"{API}/tools/{status_tool['id']}/execute", headers=dev, json={"arguments": {"service": "auth"}})
    assert r.json()["policy"]["allowed"] and '"healthy"' in r.json()["result"]["content"]
    bad = await client.post(f"{API}/tools/{status_tool['id']}/execute", headers=dev, json={"arguments": {"nope": 1}})
    assert bad.json()["policy"]["rule"] == "argument_schema"
    h = await client.post(f"{API}/mcp/servers/{sid}/health", headers=admin)
    assert h.json()["status"] == "ok"
    await client.post(f"{API}/mcp/servers/{sid}/disconnect", headers=admin)


async def test_mcp_rejects_disallowed_stdio_command(client, admin):
    r = await client.post(f"{API}/mcp/servers", headers=admin, json={"name": "evil", "transport": "stdio", "command": "/bin/rm",
                                                                     "args": ["-rf", "/"]})
    assert r.status_code == 409


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_mcp_streamable_http_with_api_key_auth(client, admin):
    port = _free_port()
    env = {**os.environ, "DEMO_MCP_API_KEY": "test-key-xyz"}
    proc = subprocess.Popen([sys.executable, str(DEMO), "--http", str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                await asyncio.sleep(0.1)
        url = f"http://127.0.0.1:{port}/mcp"
        ok = await client.post(f"{API}/mcp/servers", headers=admin, json={
            "name": "http-ok", "transport": "streamable_http", "url": url, "auth_type": "api_key",
            "credentials": {"api_key": "test-key-xyz", "header": "X-API-Key"}})
        c = await client.post(f"{API}/mcp/servers/{ok.json()['id']}/connect", headers=admin)
        assert c.status_code == 200, c.text
        assert "list_incidents" in {t["name"] for t in c.json()["tools"]}
        bad = await client.post(f"{API}/mcp/servers", headers=admin, json={
            "name": "http-bad", "transport": "streamable_http", "url": url, "auth_type": "api_key",
            "credentials": {"api_key": "wrong", "header": "X-API-Key"}})
        c2 = await client.post(f"{API}/mcp/servers/{bad.json()['id']}/connect", headers=admin)
        assert c2.status_code == 502
        await client.post(f"{API}/mcp/servers/{ok.json()['id']}/disconnect", headers=admin)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


async def test_malicious_tool_output_is_flagged(client, admin, aieng):
    tools = (await client.get(f"{API}/tools", headers=admin)).json()
    t = next(x for x in tools if x["qualified_name"] == "aiecc-demo-ops/fetch_untrusted_page")
    r = await client.post(f"{API}/tools/{t['id']}/execute", headers=aieng, json={"arguments": {"url": "https://vendor.example"}})
    res = r.json()["result"]
    assert res["ok"] and res["injection_flags"], "prompt injection inside tool output must be flagged"
