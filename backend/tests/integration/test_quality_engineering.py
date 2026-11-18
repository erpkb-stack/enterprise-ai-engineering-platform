"""Integration: prompts, RAG ingestion (all formats), evaluations, red team, experiments, workflows,
architecture lab, code review, metrics + load test, dashboard."""

from __future__ import annotations

import io

import pytest

from tests.conftest import API, wait_for

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _pdf_bytes(text: str) -> bytes:
    """Minimal valid single-page PDF with a text stream (no external PDF library needed)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def _docx_bytes(text: str) -> bytes:
    import docx

    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize("filename,data", [
    ("ops-handbook.txt", b"The ops handbook says canary deployments run for 30 minutes before full rollout to production."),
    ("faq.md", b"# FAQ\n\nThe Zephyr cluster autoscaler targets 65 percent CPU utilisation across all node pools."),
    ("services.csv", b"service,owner,tier\nbilling,finance-eng,tier1\nsearch,discovery,tier2\n"),
    ("pdf-guide.pdf", _pdf_bytes("Quarterly disaster recovery drills restore the primary database within 4 hours.")),
    ("word-guide.docx", _docx_bytes("Kubernetes namespaces are allocated per team with resource quotas enforced.")),
])
async def test_upload_all_supported_formats(client, aieng, filename, data):
    r = await client.post(f"{API}/knowledge/documents", headers=aieng, files={"file": (filename, data)},
                          data={"collection": "uploads", "chunk_size": "200", "chunk_overlap": "20"})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "ready" and doc["chunk_count"] >= 1
    chunks = (await client.get(f"{API}/knowledge/documents/{doc['id']}/chunks", headers=aieng)).json()["chunks"]
    assert chunks[0]["char_end"] > chunks[0]["char_start"]


async def test_upload_rejects_bad_files_and_duplicates(client, aieng):
    assert (await client.post(f"{API}/knowledge/documents", headers=aieng, files={"file": ("x.exe", b"MZ..")})).status_code == 422
    assert (await client.post(f"{API}/knowledge/documents", headers=aieng, files={"file": ("x.pdf", b"not a pdf")})).status_code == 422
    dup = (await client.post(f"{API}/knowledge/documents", headers=aieng, data={"collection": "uploads"},
                             files={"file": ("ops-handbook-copy.txt", b"The ops handbook says canary deployments run for 30 "
                                                                       b"minutes before full rollout to production.")}))
    assert dup.status_code == 409


@pytest.mark.parametrize("strategy", ["semantic", "keyword", "hybrid", "hybrid_rerank"])
async def test_retrieval_strategies_return_scored_provenance(client, aieng, strategy):
    r = await client.post(f"{API}/knowledge/search", headers=aieng,
                          json={"query": "refund within 30 days of purchase", "strategy": strategy, "top_k": 3,
                                "collection": "engineering", "compress": True})
    body = r.json()
    assert r.status_code == 200 and body["hits"], body
    top = body["hits"][0]
    assert top["filename"] == "refund-and-warranty-policy.md"
    assert top["chunk_id"] and top["document_id"] and "char_start" in top
    if strategy == "hybrid_rerank":
        assert "rerank" in top["scores"] and "rrf" in top["scores"]
    assert "compressed" in top


async def test_metadata_filter(client, aieng):
    r = await client.post(f"{API}/knowledge/search", headers=aieng, json={"query": "policy", "metadata": {"source": "demo"},
                                                                          "strategy": "keyword"})
    assert r.json()["hits"] and all(h["metadata"].get("source") == "demo" for h in r.json()["hits"])


async def test_grounded_answer_with_verified_citations(client, aieng):
    r = await client.post(f"{API}/knowledge/answer", headers=aieng,
                          json={"query": "How long do customers have to request a refund?", "strategy": "hybrid_rerank"})
    body = r.json()
    assert "[" in body["answer"] and body["verification"]["valid_citations"] >= 1
    assert body["verification"]["citation_precision"] == 1.0 and body["is_mock"]


async def test_prompt_lifecycle_preview_test_compare_and_approval(client, aieng, security, admin):
    r = await client.post(f"{API}/prompts", headers=aieng, json={
        "name": "test-prompt", "description": "t",
        "version": {"system": "Answer from sources.", "user_template": "Q: {{ question }}",
                    "model_config": {"provider": "mock", "model": "mock-reasoner-1"}}})
    assert r.status_code == 201
    tid = r.json()["id"]
    assert r.json()["versions"][0]["variables"] == ["question"]
    pv = await client.post(f"{API}/prompts/{tid}/preview", headers=aieng, json={"variables": {"question": "refund window?"}})
    assert pv.json()["token_estimate"]["total"] > 0 and pv.json()["messages"][-1]["content"] == "Q: refund window?"
    missing = await client.post(f"{API}/prompts/{tid}/preview", headers=aieng, json={"variables": {}})
    assert missing.status_code == 422
    t = await client.post(f"{API}/prompts/{tid}/test", headers=aieng, json={
        "variables": {"question": "How long is the refund window?"}, "use_rag": True, "repeats": 2,
        "must_contain": ["30 days"]})
    chk = t.json()["checks"]
    assert chk["grounding"]["valid_citations"] >= 1 and chk["instruction_adherence"] == 1.0 and chk["consistency"] == 1.0
    v2 = await client.post(f"{API}/prompts/{tid}/versions", headers=aieng, json={
        "system": "Strict.", "user_template": "{{ sources }}\nQuestion: {{ question }}"})
    assert v2.json()["version"] == 2
    cmp = await client.post(f"{API}/prompts/{tid}/compare", headers=aieng, json={
        "version_a": 1, "version_b": 2, "variables": {"question": "refund window"}, "use_rag": True})
    assert cmp.json()["a"]["version"] == 1 and cmp.json()["b"]["version"] == 2
    assert (await client.post(f"{API}/prompts/{tid}/versions/2/submit", headers=aieng)).json()["status"] == "in_review"
    assert (await client.post(f"{API}/prompts/{tid}/versions/2/review", headers=aieng,
                              json={"decision": "approved"})).status_code == 403, "AI Engineer lacks prompts:approve"
    ok = await client.post(f"{API}/prompts/{tid}/versions/2/review", headers=security, json={"decision": "approved"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"


async def test_separation_of_duties_on_prompt_approval(client, admin):
    r = await client.post(f"{API}/prompts", headers=admin, json={"name": "sod-prompt",
                                                                "version": {"user_template": "{{ question }}"}})
    tid = r.json()["id"]
    await client.post(f"{API}/prompts/{tid}/versions/1/submit", headers=admin)
    r = await client.post(f"{API}/prompts/{tid}/versions/1/review", headers=admin, json={"decision": "approved"})
    assert r.status_code == 403 and "separation of duties" in r.json()["error"]["message"]


async def test_structured_output_validation(client, aieng):
    tpl = next(t for t in (await client.get(f"{API}/prompts", headers=aieng)).json()["items"] if t["name"] == "incident-summary")
    r = await client.post(f"{API}/prompts/{tpl['id']}/test", headers=aieng, json={"variables": {"incident": "checkout slow"}})
    assert r.json()["checks"]["structured_valid"] == 1.0


async def _completed(client, h, url):
    r = (await client.get(url, headers=h)).json()
    return r if r.get("status") in ("completed", "failed") else None


async def test_evaluation_run_report_and_regression(client, aieng):
    ds = next(d for d in (await client.get(f"{API}/evaluations/datasets", headers=aieng)).json() if "Golden" in d["name"])
    r = await client.post(f"{API}/evaluations/run", headers=aieng, json={
        "dataset_id": ds["id"], "target_type": "rag", "config": {"strategy": "hybrid_rerank", "top_k": 4, "llm_judge": True}})
    assert r.status_code == 202
    run = await wait_for(lambda: _completed(client, aieng, f"{API}/evaluations/{r.json()['id']}"), timeout=120)
    assert run["status"] == "completed", run
    s = run["summary"]
    assert s["cases"] == 6 and s["pass_rate"] is not None and "groundedness" in s["metrics"]
    assert s["llm_judge"]["limitations"]
    res = (await client.get(f"{API}/evaluations/{run['id']}/results", headers=aieng)).json()
    assert len(res["results"]) == 6 and all("reference_f1" in x["scores"] for x in res["results"])
    r2 = await client.post(f"{API}/evaluations/run", headers=aieng, json={
        "dataset_id": ds["id"], "target_type": "rag", "baseline_run_id": run["id"], "config": {"strategy": "keyword", "top_k": 2}})
    run2 = await wait_for(lambda: _completed(client, aieng, f"{API}/evaluations/{r2.json()['id']}"), timeout=120)
    comp = run2["summary"]["comparison"]
    assert comp["baseline_run_id"] == run["id"] and "metric_deltas" in comp


async def test_agent_and_prompt_evaluation_targets(client, aieng, admin):
    ds = next(d for d in (await client.get(f"{API}/evaluations/datasets", headers=aieng)).json() if "Golden" in d["name"])
    from tests.conftest import agent_id

    aid = await agent_id(client, admin, "Enterprise Research Agent")
    r = await client.post(f"{API}/evaluations/run", headers=aieng, json={"dataset_id": ds["id"], "target_type": "agent",
                                                                          "target_id": aid})
    run = await wait_for(lambda: _completed(client, aieng, f"{API}/evaluations/{r.json()['id']}"), timeout=180)
    assert run["status"] == "completed" and run["summary"]["cases"] == 6
    tpl = next(t for t in (await client.get(f"{API}/prompts", headers=aieng)).json()["items"] if t["name"] == "grounded-qa")
    r = await client.post(f"{API}/evaluations/run", headers=aieng, json={"dataset_id": ds["id"], "target_type": "prompt",
                                                                          "target_id": tpl["id"], "config": {"use_rag": True}})
    run = await wait_for(lambda: _completed(client, aieng, f"{API}/evaluations/{r.json()['id']}"), timeout=120)
    assert run["status"] == "completed" and run["target_version"] == 2


async def test_red_team_suite_platform_controls_defend(client, aieng):
    r = await client.post(f"{API}/redteam/run", headers=aieng, json={"provider": "mock", "model": "mock-reasoner-1"})
    assert r.status_code == 202
    run = await wait_for(lambda: _completed(client, aieng, f"{API}/evaluations/{r.json()['id']}"), timeout=120)
    res = {x["scenario_id"]: x for x in run["summary"]["results"]}
    for sid in ("tool-unauth-01", "tool-unauth-02", "tool-malicious-01", "tenant-01", "autonomy-01"):
        assert res[sid]["outcome"] == "defended", res[sid]
        assert res[sid]["tests"] == "platform_control"
    assert run["summary"]["sandbox"] and run["summary"]["caveat"]
    assert res["autonomy-01"]["evidence"]["steps"] <= 3


async def test_experiment_paired_run_with_significance_guard(client, aieng):
    exp = next(e for e in (await client.get(f"{API}/experiments", headers=aieng)).json() if "v1 vs v2" in e["name"])
    r = await client.post(f"{API}/experiments/{exp['id']}/run", headers=aieng, json={"mode": "paired"})
    assert r.status_code == 200, r.text
    res = r.json()["results"]
    assert all(v["n"] >= 6 for v in res["variants"])
    m = res["comparisons"][0]["metrics"]["quality_score"]
    assert m["significant"] is False and m["conclusion"] == "insufficient_sample"
    a1 = (await client.get(f"{API}/experiments/{exp['id']}/assign/user-42", headers=aieng)).json()
    a2 = (await client.get(f"{API}/experiments/{exp['id']}/assign/user-42", headers=aieng)).json()
    assert a1 == a2, "assignment is sticky"


async def test_experiment_requires_single_control(client, aieng):
    r = await client.post(f"{API}/experiments", headers=aieng, json={"name": "bad", "dimension": "model", "variants": [
        {"name": "a", "is_control": True}, {"name": "b", "is_control": True}]})
    assert r.status_code == 422


async def _wf(client, h):
    return next(w for w in (await client.get(f"{API}/workflows", headers=h)).json() if "Incident Triage" in w["name"])


async def test_workflow_approval_branch(client, aieng, security):
    wf = await _wf(client, aieng)
    r = await client.post(f"{API}/workflows/{wf['id']}/execute", headers=aieng, json={
        "input": {"question": "What is the Sev2 acknowledgement time?", "severity": "SEV2", "service": "checkout"}})
    ex = r.json()
    assert ex["status"] == "waiting_approval" and ex["paused_at_node"] == "appr"
    assert (await client.post(f"{API}/workflow-executions/{ex['id']}/approve", headers=aieng,
                              json={"decision": "approved"})).status_code == 403
    ok = (await client.post(f"{API}/workflow-executions/{ex['id']}/approve", headers=security,
                            json={"decision": "approved", "comment": "page owner"})).json()
    assert ok["status"] == "completed", ok
    trace = [t["node_id"] for t in ok["trace"]]
    assert trace[:3] == ["in", "kb", "sum"] and "status" in trace and "fmt" not in trace
    assert ok["state"]["node_status"]["fmt"] == "skipped"
    assert "degraded" in str(ok["output"]), (ok["output"], ok["trace"][-2:], ok["state"]["node_status"])


async def test_workflow_low_severity_skips_approval(client, aieng):
    wf = await _wf(client, aieng)
    ex = (await client.post(f"{API}/workflows/{wf['id']}/execute", headers=aieng, json={
        "input": {"question": "refund window", "severity": "sev3", "service": "search"}})).json()
    assert ex["status"] == "completed" and "Logged for business hours" in str(ex["output"])
    assert ex["state"]["node_status"]["appr"] == "skipped" and ex["state"]["node_status"]["status"] == "skipped"


async def test_workflow_breakpoint_step_debugging(client, aieng):
    wf = await _wf(client, aieng)
    ex = (await client.post(f"{API}/workflows/{wf['id']}/execute", headers=aieng, json={
        "input": {"question": "x", "severity": "sev3", "service": "auth"}, "breakpoints": ["sum"]})).json()
    assert ex["status"] == "paused" and ex["paused_at_node"] == "sum" and len(ex["trace"]) == 2
    step = (await client.post(f"{API}/workflow-executions/{ex['id']}/resume", headers=aieng, json={"step": True})).json()
    assert step["status"] == "paused" and len(step["trace"]) == 3
    done = (await client.post(f"{API}/workflow-executions/{ex['id']}/resume", headers=aieng, json={})).json()
    assert done["status"] == "completed"


async def test_workflow_error_handler_and_retries(client, aieng):
    graph = {"nodes": [
        {"id": "in", "type": "input", "data": {}},
        {"id": "bad", "type": "mcp_tool", "data": {"config": {"tool": "calculator", "arguments": {"expression": "1/0"},
                                                              "retry": {"max_attempts": 2}}}},
        {"id": "eh", "type": "error_handler", "data": {"config": {"message": "handled"}}},
        {"id": "out", "type": "output", "data": {}}],
        "edges": [{"id": "1", "source": "in", "target": "bad"}, {"id": "2", "source": "bad", "target": "out"},
                  {"id": "3", "source": "bad", "target": "eh", "sourceHandle": "error"},
                  {"id": "4", "source": "eh", "target": "out"}]}
    r = await client.post(f"{API}/workflows", headers=aieng, json={"name": "error-wf", "graph": graph})
    assert r.status_code == 201, r.text
    ex = (await client.post(f"{API}/workflows/{r.json()['id']}/execute", headers=aieng, json={"input": {}})).json()
    assert ex["status"] == "completed"
    assert ex["state"]["node_status"]["bad"] == "failed" and ex["state"]["node_status"]["eh"] == "completed"
    assert (await client.post(f"{API}/workflows", headers=aieng, json={"name": "cyclic", "graph": {
        "nodes": [{"id": "i", "type": "input"}], "edges": []}})).status_code == 422


async def test_workflow_api_node_ssrf_blocked(client, aieng):
    graph = {"nodes": [{"id": "in", "type": "input"}, {"id": "api", "type": "api_request",
                                                       "data": {"config": {"url": "http://169.254.169.254/latest/meta-data"}}},
                       {"id": "out", "type": "output"}],
             "edges": [{"id": "1", "source": "in", "target": "api"}, {"id": "2", "source": "api", "target": "out"}]}
    r = await client.post(f"{API}/workflows", headers=aieng, json={"name": "ssrf-wf", "graph": graph})
    ex = (await client.post(f"{API}/workflows/{r.json()['id']}/execute", headers=aieng, json={"input": {}})).json()
    assert ex["status"] == "failed" and "blocked" in ex["error"]


async def test_architecture_review_and_adr(client, aieng):
    d = next(x for x in (await client.get(f"{API}/architecture/designs", headers=aieng)).json() if "Reference" in x["name"])
    rep = (await client.post(f"{API}/architecture/designs/{d['id']}/review", headers=aieng, json={"ai_narrative": True})).json()
    assert set(rep["scores"]) >= {"scalability", "availability", "security", "cost"} and rep["findings"]
    assert rep["ai_narrative"]["is_mock"] and rep["metrics"]["estimated_monthly_cost_usd"] > 0
    seq = (await client.post(f"{API}/architecture/designs/{d['id']}/sequence", headers=aieng)).json()
    assert seq["sequence_diagram"].startswith("sequenceDiagram")
    adr = (await client.post(f"{API}/architecture/adrs", headers=aieng, json={"title": "Use pgvector", "status": "accepted",
                                                                              "decision": "pgvector over a separate vector DB"})).json()
    assert adr["number"] >= 2


async def test_code_review_separates_static_and_ai(client, dev):
    r = await client.post(f"{API}/code-review", headers=dev, json={"code": "import time\nasync def f():\n    time.sleep(1)\n"})
    body = r.json()
    assert body["static"]["findings"][0]["source"] == "static-analysis"
    assert body["ai"]["source"] == "ai-generated" and body["ai"]["is_mock"]


async def test_metrics_are_measured_and_loadtest_tags_synthetic(client, admin):
    m = (await client.get(f"{API}/metrics", headers=admin)).json()
    assert m["http_total"]["count"] > 20 and m["http_total"]["p95_ms"] is not None
    assert any(r["name"].startswith("POST /agents/{agent_id}/execute") for r in m["routes"])
    lt = (await client.post(f"{API}/performance/loadtest", headers=admin,
                            json={"target": "list_agents", "requests": 60, "concurrency": 10, "transport": "asgi"})).json()
    assert lt["errors"] == 0 and lt["p99_ms"] >= lt["p50_ms"] > 0 and "SYNTHETIC" in lt["label"]
    m2 = (await client.get(f"{API}/metrics", headers=admin)).json()
    assert m2["http_total"]["synthetic_count"] >= 60
    organic = (await client.get(f"{API}/metrics", headers=admin, params={"include_synthetic": False})).json()
    assert organic["http_total"]["synthetic_count"] == 0
    prom = await client.get("/metrics")
    assert "aiecc_http_request_duration_seconds_bucket" in prom.text


async def test_dashboard_overview_reflects_real_activity(client, admin):
    o = (await client.get(f"{API}/dashboard/overview", headers=admin)).json()
    assert o["agents"]["active"] >= 6 and o["executions"]["total"] >= 1
    assert o["model_usage"] and o["model_usage"][0]["is_mock"]
    assert o["mcp"]["enabled_tools"] >= 6 and o["security_events"]


async def test_readiness_and_openapi(client):
    r = (await client.get("/ready")).json()
    assert r["database"]["status"] == "ok"
    spec = (await client.get("/openapi.json")).json()
    for p in ("/api/v1/agents", "/api/v1/agents/{agent_id}/execute", "/api/v1/mcp/servers/{server_id}/connect",
              "/api/v1/prompts/{template_id}/test", "/api/v1/evaluations/run", "/api/v1/evaluations/{run_id}/results",
              "/api/v1/experiments/{experiment_id}/results", "/api/v1/knowledge/documents", "/api/v1/knowledge/search",
              "/api/v1/workflows/{workflow_id}/execute", "/api/v1/metrics", "/api/v1/audit/events"):
        assert p in spec["paths"], p


async def test_websocket_requires_auth_handshake(app, admin):
    from starlette.testclient import TestClient

    with TestClient(app) as tc:
        with tc.websocket_connect(f"{API}/ws/events") as ws:
            ws.send_json({"type": "auth", "token": "bad"})
            with pytest.raises(Exception):
                ws.receive_json()
