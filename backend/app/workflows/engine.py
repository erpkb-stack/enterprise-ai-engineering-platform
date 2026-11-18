"""Workflow engine: validated DAGs executed with dead-path elimination, resumable pauses
(human approval, breakpoints/step debugging), per-node retries and error-handler routing.

Execution state is persisted after every node so a paused execution can resume in a later request.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import time
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

import httpx
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.core.errors import ValidationFailedError
from app.core.ids import utcnow
from app.core.safe_eval import safe_eval
from app.integrations.llm.base import ChatMessage, CompletionRequest
from app.integrations.llm.gateway import Route, gateway
from app.models import Agent, AgentExecution, WorkflowExecution
from app.observability import metrics
from app.observability.events import bus
from app.observability.tracing import span
from app.rag import service as rag
from app.security.principal import Principal
from app.services.tool_service import invoke_tool

NODE_TYPES = {"input", "llm", "agent", "mcp_tool", "api_request", "rag_retrieval", "condition", "python_function",
              "human_approval", "transform", "output", "error_handler"}
_env = SandboxedEnvironment(undefined=StrictUndefined)


# --- registered (not arbitrary) python functions -------------------------------------------------
def _word_count(v: Any) -> int:
    return len(str(v).split())


def _extract_json(v: Any) -> Any:
    m = re.search(r"\{.*\}|\[.*\]", str(v), re.S)
    return json.loads(m.group(0)) if m else None


def _regex_extract(v: Any, pattern: str = r"\w+") -> list[str]:
    if len(pattern) > 200:
        raise ValueError("pattern too long")
    return re.findall(pattern, str(v))[:100]


PY_FUNCTIONS = {"word_count": _word_count, "extract_json": _extract_json, "regex_extract": _regex_extract,
                "upper": lambda v: str(v).upper(), "lower": lambda v: str(v).lower(),
                "strip_html": lambda v: re.sub(r"<[^>]+>", "", str(v))}


class NodeError(Exception):
    pass


class Paused(Exception):
    def __init__(self, node_id: str, reason: str) -> None:
        self.node_id, self.reason = node_id, reason


def validate_graph(graph: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    ids = [n.get("id") for n in nodes]
    if len(nodes) > get_settings().workflow_max_nodes:
        errs.append(f"too many nodes (max {get_settings().workflow_max_nodes})")
    if len(set(ids)) != len(ids):
        errs.append("duplicate node ids")
    types = {n["id"]: n.get("type") for n in nodes if n.get("id")}
    for n in nodes:
        if n.get("type") not in NODE_TYPES:
            errs.append(f"node {n.get('id')}: unknown type '{n.get('type')}'")
    if sum(1 for t in types.values() if t == "input") != 1:
        errs.append("workflow must have exactly one input node")
    if not any(t == "output" for t in types.values()):
        errs.append("workflow must have at least one output node")
    adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s not in types or t not in types:
            errs.append(f"edge {e.get('id')}: references unknown node")
            continue
        if types[t] == "input":
            errs.append(f"edge {e.get('id')}: input node cannot have incoming edges")
        h = e.get("sourceHandle")
        if types[s] == "condition" and h not in ("true", "false"):
            errs.append(f"edge {e.get('id')}: condition edges need sourceHandle 'true' or 'false'")
        if types[s] == "human_approval" and h not in (None, "approved", "rejected", "error"):
            errs.append(f"edge {e.get('id')}: approval edges use 'approved' or 'rejected'")
        adj[s].append(t)
    # cycle detection (Kahn)
    indeg = {i: 0 for i in types}
    for ts in adj.values():
        for t in ts:
            indeg[t] += 1
    q = [i for i, d in indeg.items() if d == 0]
    seen = 0
    while q:
        x = q.pop()
        seen += 1
        for t in adj[x]:
            indeg[t] -= 1
            if indeg[t] == 0:
                q.append(t)
    if seen != len(types):
        errs.append("workflow graph contains a cycle")
    for n in nodes:
        cfg = (n.get("data") or {}).get("config") or {}
        t = n.get("type")
        if t == "condition" and not cfg.get("expression"):
            errs.append(f"node {n['id']}: condition requires an expression")
        if t == "mcp_tool" and not cfg.get("tool"):
            errs.append(f"node {n['id']}: mcp_tool requires a tool")
        if t == "agent" and not cfg.get("agent_id"):
            errs.append(f"node {n['id']}: agent node requires agent_id")
        if t == "api_request" and not cfg.get("url"):
            errs.append(f"node {n['id']}: api_request requires a url")
        if t == "python_function" and cfg.get("function") not in PY_FUNCTIONS:
            errs.append(f"node {n['id']}: python_function must be one of {sorted(PY_FUNCTIONS)}")
    return errs


def _render(tpl: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(tpl, str):
        return _env.from_string(tpl).render(**ctx) if "{{" in tpl or "{%" in tpl else tpl
    if isinstance(tpl, dict):
        return {k: _render(v, ctx) for k, v in tpl.items()}
    if isinstance(tpl, list):
        return [_render(v, ctx) for v in tpl]
    return tpl


def _check_url(url: str) -> None:
    """SSRF guard: https (or http to allowlisted hosts), allowlisted hostnames only, no private IP literals."""
    p = urlparse(url)
    if p.scheme not in ("https", "http"):
        raise NodeError("only http(s) URLs are allowed")
    host = (p.hostname or "").lower()
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise NodeError("private/loopback addresses are blocked")
    except ValueError:
        pass
    if host not in get_settings().http_tool_allowed_hosts:
        raise NodeError(f"host '{host}' is not in AIECC_HTTP_TOOL_ALLOWED_HOSTS")


class WorkflowRunner:
    def __init__(self, db: AsyncSession, ex: WorkflowExecution, graph: dict[str, Any], principal: Principal) -> None:
        self.db, self.ex, self.principal = db, ex, principal
        self.nodes = {n["id"]: n for n in graph["nodes"]}
        self.edges = graph.get("edges") or []
        self.incoming: dict[str, list[dict]] = defaultdict(list)
        self.outgoing: dict[str, list[dict]] = defaultdict(list)
        for e in self.edges:
            self.incoming[e["target"]].append(e)
            self.outgoing[e["source"]].append(e)

    @property
    def state(self) -> dict[str, Any]:
        return self.ex.state

    def ctx(self) -> dict[str, Any]:
        outs = self.state["outputs"]
        return {"input": self.ex.input, "nodes": outs, "last": self.state.get("last")}

    async def _exec_node(self, nid: str) -> tuple[Any, str | None]:
        """Returns (output, active_handle). active_handle selects outgoing edges for branching nodes."""
        n = self.nodes[nid]
        t = n["type"]
        cfg = (n.get("data") or {}).get("config") or {}
        c = self.ctx()
        org = self.ex.organization_id
        if t == "input":
            return self.ex.input, None
        if t == "llm":
            prompt = _render(cfg.get("prompt", "{{ last }}"), c)
            msgs = ([ChatMessage(role="system", content=_render(cfg["system"], c))] if cfg.get("system") else [])
            msgs.append(ChatMessage(role="user", content=str(prompt)))
            resp, meta = await gateway.complete(
                CompletionRequest(messages=msgs, model=cfg.get("model", "mock-reasoner-1"),
                                  temperature=float(cfg.get("temperature", 0.2)), max_tokens=int(cfg.get("max_tokens", 600))),
                route=Route(cfg.get("provider", "mock"), cfg.get("model", "mock-reasoner-1")), db=self.db, org_id=org,
                purpose="workflow_llm")
            return {"text": resp.content, "is_mock": resp.is_mock, "model": meta["model"], "tokens": resp.tokens_in + resp.tokens_out}, None
        if t == "agent":
            from app.agents import runtime

            agent = (await self.db.execute(select(Agent).where(Agent.id == cfg["agent_id"], Agent.organization_id == org))).scalar_one_or_none()
            if agent is None:
                raise NodeError("agent not found")
            ax = await runtime.create_execution(self.db, org_id=org, agent=agent, principal=self.principal,
                                                input_text=str(_render(cfg.get("input", "{{ last }}"), c)))
            await self.db.commit()
            await runtime._run(ax.id, self.principal)
            row = (await self.db.execute(select(AgentExecution).where(AgentExecution.id == ax.id))).scalar_one()
            await self.db.refresh(row)
            if row.status != "succeeded":
                raise NodeError(f"agent execution {row.status}: {row.error}")
            return {"text": row.output, "execution_id": row.id, "citations": row.citations}, None
        if t == "mcp_tool":
            args = _render(cfg.get("arguments") or {}, c)
            if isinstance(args, str):
                args = json.loads(args)
            res, decision = await invoke_tool(self.db, org_id=org, principal=self.principal, qualified=cfg["tool"],
                                              arguments=args, context="workflow", workflow_execution_id=self.ex.id,
                                              approved=bool(self.state.get("approvals", {}).get(nid)))
            if not res.ok:
                raise NodeError(res.error or "tool failed")
            return {"text": res.content, "structured": res.structured, "injection_flags": res.injection_flags}, None
        if t == "api_request":
            url = str(_render(cfg["url"], c))
            _check_url(url)
            async with httpx.AsyncClient(timeout=float(cfg.get("timeout_s", 10)), follow_redirects=False) as client:
                r = await client.request(cfg.get("method", "GET").upper(), url, headers=_render(cfg.get("headers") or {}, c),
                                         json=_render(cfg.get("body"), c) if cfg.get("body") else None)
            body: Any = r.text[:20_000]
            try:
                body = r.json()
            except ValueError:
                pass
            if r.status_code >= 400:
                raise NodeError(f"HTTP {r.status_code}")
            return {"status": r.status_code, "body": body}, None
        if t == "rag_retrieval":
            res = await rag.search(self.db, org_id=org, query=str(_render(cfg.get("query", "{{ input.question }}"), c)),
                                   strategy=cfg.get("strategy", "hybrid"), top_k=int(cfg.get("top_k", 4)),
                                   collection=cfg.get("collection"))
            return {"passages": [{"filename": h["filename"], "content": h["content"], "scores": h["scores"]} for h in res["hits"]],
                    "context": rag.format_sources(res["hits"])}, None
        if t == "condition":
            val = bool(safe_eval(cfg["expression"], c))
            return {"result": val}, "true" if val else "false"
        if t == "python_function":
            if not get_settings().flag("workflow_python_functions"):
                raise NodeError("python_function nodes are disabled by feature flag")
            fn = PY_FUNCTIONS[cfg["function"]]
            arg = _render(cfg.get("argument", "{{ last }}"), c)
            return fn(arg, **(cfg.get("kwargs") or {})), None
        if t == "transform":
            if cfg.get("expression"):
                return safe_eval(cfg["expression"], c), None
            return _render(cfg.get("template", "{{ last }}"), c), None
        if t == "human_approval":
            decision = self.state.get("approvals", {}).get(nid)
            if decision is None:
                raise Paused(nid, "approval")
            return {"approved": decision == "approved", "by": self.state.get("approvers", {}).get(nid)}, decision
        if t == "output":
            return _render(cfg.get("template", "{{ last }}"), c) if cfg.get("template") else c["last"], None
        if t == "error_handler":
            return {"handled_error": self.state.get("last_error"), "message": cfg.get("message", "error handled")}, None
        raise NodeError(f"unsupported node type {t}")

    def _resolve_edges(self, nid: str, handle: str | None, failed: bool) -> None:
        es = self.state["edges"]
        for e in self.outgoing[nid]:
            h = e.get("sourceHandle")
            if failed:
                taken = h == "error"
            elif h == "error":
                taken = False
            elif handle is not None and self.nodes[nid]["type"] in ("condition", "human_approval"):
                taken = h == handle or (h is None and handle == "approved")
            else:
                taken = True
            es[e["id"]] = "taken" if taken else "skipped"
        for e in self.outgoing[nid]:
            self._maybe_ready(e["target"])

    def _maybe_ready(self, nid: str) -> None:
        st = self.state
        if nid in st["node_status"]:
            return
        ins = self.incoming[nid]
        statuses = [st["edges"].get(e["id"]) for e in ins]
        if any(s is None for s in statuses):
            return
        if any(s == "taken" for s in statuses):
            if nid not in st["queue"]:
                st["queue"].append(nid)
        else:  # dead path elimination
            st["node_status"][nid] = "skipped"
            for e in self.outgoing[nid]:
                st["edges"][e["id"]] = "skipped"
            for e in self.outgoing[nid]:
                self._maybe_ready(e["target"])

    async def _save(self) -> None:
        # mutate in place (keeps local references valid) and mark the JSON column dirty
        flag_modified(self.ex, "state")
        self.ex.trace = list(self.ex.trace)
        await self.db.commit()

    async def run(self, step_once: bool = False) -> None:
        st = self.state
        bps = set(self.ex.breakpoints or [])
        t_start = time.perf_counter()
        while st["queue"]:
            nid = st["queue"][0]
            if nid in bps and st.get("resume_past") != nid:
                raise Paused(nid, "breakpoint")
            st["resume_past"] = None
            n = self.nodes[nid]
            cfg = (n.get("data") or {}).get("config") or {}
            attempts = int(cfg.get("retry", {}).get("max_attempts", 1)) if isinstance(cfg.get("retry"), dict) else 1
            t0 = time.perf_counter()
            err: str | None = None
            out, handle = None, None
            for attempt in range(1, max(1, min(attempts, 5)) + 1):
                try:
                    with span("workflow.node", node=nid, type=n["type"], attempt=attempt):
                        out, handle = await asyncio.wait_for(self._exec_node(nid), timeout=float(cfg.get("timeout_s", 120)))
                    err = None
                    break
                except Paused:
                    raise
                except Exception as exc:  # noqa: BLE001
                    err = f"{type(exc).__name__}: {exc}"[:500]
                    if attempt < attempts:
                        metrics.record_retry("workflow_node", n["type"])
                        await asyncio.sleep(min(2.0, 0.2 * 2**attempt))
            latency = (time.perf_counter() - t0) * 1000
            st["queue"].pop(0)
            entry = {"node_id": nid, "type": n["type"], "label": (n.get("data") or {}).get("label", nid),
                     "status": "failed" if err else "completed", "latency_ms": round(latency, 1), "error": err,
                     "output_preview": json.dumps(out, default=str)[:1500] if out is not None else None,
                     "handle": handle, "ts": time.time()}
            self.ex.trace = [*self.ex.trace, entry]
            bus.publish(self.ex.organization_id, "workflow.node", {"execution_id": self.ex.id, **entry})
            if err:
                st["node_status"][nid] = "failed"
                st["last_error"] = {"node": nid, "error": err}
                has_handler = any(e.get("sourceHandle") == "error" for e in self.outgoing[nid])
                if not has_handler:
                    raise NodeError(f"node '{nid}' failed: {err}")
                self._resolve_edges(nid, None, failed=True)
            else:
                st["node_status"][nid] = "completed"
                st["outputs"][nid] = out
                st["last"] = out
                if n["type"] == "output":
                    st["final"] = out
                self._resolve_edges(nid, handle, failed=False)
            await self._save()
            if step_once and st["queue"]:
                raise Paused(st["queue"][0], "step")
        self.ex.latency_ms += (time.perf_counter() - t_start) * 1000


def initial_state(graph: dict[str, Any]) -> dict[str, Any]:
    start = next(n["id"] for n in graph["nodes"] if n["type"] == "input")
    return {"outputs": {}, "node_status": {}, "edges": {}, "queue": [start], "approvals": {}, "approvers": {},
            "last": None, "final": None, "resume_past": None}


async def drive(db: AsyncSession, ex: WorkflowExecution, graph: dict[str, Any], principal: Principal,
                step_once: bool = False) -> WorkflowExecution:
    runner = WorkflowRunner(db, ex, graph, principal)
    ex.status, ex.paused_at_node, ex.pause_reason = "running", None, None
    t0 = time.perf_counter()
    try:
        await runner.run(step_once=step_once)
        ex.status = "completed"
        ex.output = {"result": ex.state.get("final")}
        ex.finished_at = utcnow()
    except Paused as p:
        ex.status = "waiting_approval" if p.reason == "approval" else "paused"
        ex.paused_at_node, ex.pause_reason = p.node_id, p.reason
    except Exception as exc:  # noqa: BLE001
        ex.status, ex.error = "failed", str(exc)[:2000]
        ex.finished_at = utcnow()
    flag_modified(ex, "state")
    metrics.record_op("workflow", ex.workflow_id, (time.perf_counter() - t0) * 1000, ex.status != "failed")
    return ex


def require_valid(graph: dict[str, Any]) -> None:
    errs = validate_graph(graph)
    if errs:
        raise ValidationFailedError("Invalid workflow graph", details={"errors": errs})
