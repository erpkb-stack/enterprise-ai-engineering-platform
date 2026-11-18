"""Architecture Review Assistant: deterministic rule engine over a component graph.

Graph node data: {label, kind, replicas, public, auth, stateful, encrypted, observability, region}
Edge data:       {protocol, sync, auth, timeout_ms, retries, circuit_breaker, latency_ms}

Produces per-quality-attribute scores, findings with severity, and a tradeoff report (alternatives
and consequences). An optional LLM narrative may be appended by the API layer and is labelled.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

CATEGORIES = ["scalability", "availability", "reliability", "security", "maintainability", "performance", "cost",
              "operational_complexity"]
STATEFUL_KINDS = {"database", "cache", "queue", "vector_db", "object_storage", "search"}
MONTHLY_COST = {"service": 70, "database": 400, "cache": 120, "queue": 90, "gateway": 60, "llm": 900, "vector_db": 300,
                "mcp_server": 50, "client": 0, "cdn": 40, "object_storage": 25, "external": 0, "search": 350,
                "worker": 70, "load_balancer": 25}
SEVERITY_PENALTY = {"critical": 30, "high": 18, "medium": 9, "low": 4}

TRADEOFFS: dict[str, list[dict[str, str]]] = {
    "spof": [
        {"option": "Run >= 2 replicas behind a load balancer", "pros": "Removes SPOF; rolling deploys",
         "cons": "~2x compute cost; needs stateless design or shared session store"},
        {"option": "Use a managed HA service (e.g. multi-AZ RDS)", "pros": "Automated failover; less ops",
         "cons": "Vendor lock-in; higher unit cost; failover still causes brief unavailability"},
    ],
    "sync_chain": [
        {"option": "Introduce async messaging (queue/event bus) for non-critical hops", "pros": "Decouples failure domains; smooths load",
         "cons": "Eventual consistency; harder debugging; need idempotent consumers and DLQs"},
        {"option": "Aggregate calls behind a BFF / cache results", "pros": "Fewer round trips; lower tail latency",
         "cons": "Cache invalidation complexity; staleness"},
    ],
    "no_timeout": [
        {"option": "Set explicit timeouts + bounded retries with jitter", "pros": "Prevents thread/connection exhaustion",
         "cons": "Need per-dependency tuning; retries can amplify load without budgets"},
        {"option": "Add circuit breakers and bulkheads", "pros": "Fail fast; isolates slow dependencies",
         "cons": "More configuration; risk of false trips"},
    ],
    "shared_db": [
        {"option": "Database-per-service with APIs/events for sharing", "pros": "Independent evolution and scaling",
         "cons": "Distributed transactions (sagas); data duplication"},
        {"option": "Keep shared DB but enforce schema ownership + read replicas", "pros": "Simple; strong consistency",
         "cons": "Coupled deploys; contention hotspots"},
    ],
    "public_no_auth": [
        {"option": "Front with an API gateway enforcing OIDC/JWT", "pros": "Centralised authN, rate limiting, audit",
         "cons": "Extra hop latency; gateway becomes critical path (needs HA)"},
    ],
    "llm_direct_data": [
        {"option": "Route model access through an AI gateway with policy + redaction", "pros": "Central PII redaction, cost and quota control",
         "cons": "Added latency (~5-20ms); another component to operate"},
    ],
    "no_cache": [
        {"option": "Add read-through cache (Redis) for hot reads", "pros": "Lower DB load and p95 latency",
         "cons": "Staleness; cache stampede protection required"},
    ],
    "no_observability": [
        {"option": "Instrument with OpenTelemetry traces/metrics/logs", "pros": "Faster MTTR; SLO tracking",
         "cons": "Telemetry storage cost; cardinality management"},
    ],
}


def _f(cat: str, sev: str, title: str, detail: str, components: list[str], rec: str, tradeoff: str | None = None) -> dict:
    return {"category": cat, "severity": sev, "title": title, "detail": detail, "components": components,
            "recommendation": rec, "tradeoff_key": tradeoff}


def review(graph: dict[str, Any], requirements: dict[str, Any] | None = None) -> dict[str, Any]:
    req = requirements or {}
    nodes = {n["id"]: {**(n.get("data") or {}), "id": n["id"]} for n in graph.get("nodes", [])}
    edges = [{**(e.get("data") or {}), "source": e["source"], "target": e["target"], "id": e.get("id")}
             for e in graph.get("edges", []) if e.get("source") in nodes and e.get("target") in nodes]
    label = lambda i: nodes[i].get("label", i)  # noqa: E731
    out_e, in_e = defaultdict(list), defaultdict(list)
    for e in edges:
        out_e[e["source"]].append(e)
        in_e[e["target"]].append(e)
    findings: list[dict] = []

    # availability: SPOFs on stateful / critical path components
    for i, n in nodes.items():
        kind = n.get("kind", "service")
        if kind in ("client", "external"):
            continue
        reps = int(n.get("replicas", 1) or 1)
        critical = bool(in_e[i]) and (kind in STATEFUL_KINDS or kind in ("gateway", "service", "load_balancer", "llm"))
        if reps < 2 and critical:
            sev = "critical" if kind in ("database", "gateway", "load_balancer") else "high"
            findings.append(_f("availability", sev, f"Single point of failure: {label(i)}",
                               f"{kind} has {reps} replica(s) and {len(in_e[i])} dependent(s).", [label(i)],
                               "Add redundancy (>=2 replicas / multi-AZ) and health-checked failover.", "spof"))
    regions = {n.get("region") for n in nodes.values() if n.get("region")}
    if req.get("availability_target", 99.9) >= 99.95 and len(regions) <= 1:
        findings.append(_f("availability", "high", "Single region for a >=99.95% target",
                           "All components run in one region; a regional outage breaches the SLO.", [],
                           "Consider active-passive multi-region for stateful tiers.", "spof"))

    # performance/scalability: longest synchronous chain
    sync_adj = defaultdict(list)
    for e in edges:
        if e.get("sync", e.get("protocol") not in ("queue", "event", "async")):
            sync_adj[e["source"]].append(e)
    memo: dict[str, tuple[int, float, list[str]]] = {}

    def longest(i: str, seen: frozenset) -> tuple[int, float, list[str]]:
        if i in memo:
            return memo[i]
        best = (0, 0.0, [i])
        for e in sync_adj[i]:
            if e["target"] in seen:
                continue
            d, lat, path = longest(e["target"], seen | {e["target"]})
            cand = (d + 1, lat + float(e.get("latency_ms", 20) or 20), [i, *path])
            if cand[0] > best[0] or (cand[0] == best[0] and cand[1] > best[1]):
                best = cand
        memo[i] = best
        return best

    chains = [longest(i, frozenset([i])) for i in nodes]
    depth, lat, path = max(chains, default=(0, 0.0, []))
    if depth >= 4:
        findings.append(_f("performance", "high" if depth >= 5 else "medium", f"Deep synchronous call chain ({depth} hops)",
                           f"{' -> '.join(label(p) for p in path)} adds ~{lat:.0f}ms baseline and multiplies tail latency.",
                           [label(p) for p in path], "Collapse hops, parallelise, or move non-critical work async.", "sync_chain"))
    budget = req.get("latency_budget_ms")
    if budget and lat > budget:
        findings.append(_f("performance", "high", "Critical path exceeds latency budget",
                           f"Estimated {lat:.0f}ms > budget {budget}ms.", [label(p) for p in path],
                           "Cache, co-locate, or remove synchronous hops on the critical path.", "sync_chain"))

    # reliability: timeouts/retries/circuit breakers on sync edges
    for e in edges:
        if e.get("protocol") in ("queue", "event", "async"):
            continue
        if not e.get("timeout_ms"):
            findings.append(_f("reliability", "medium", f"No timeout: {label(e['source'])} -> {label(e['target'])}",
                               "Unbounded waits exhaust connection pools under partial failure.",
                               [label(e["source"]), label(e["target"])], "Set an explicit timeout below the caller's own budget.",
                               "no_timeout"))
        if nodes[e["target"]].get("kind") in ("llm", "external", "mcp_server") and not e.get("circuit_breaker"):
            findings.append(_f("reliability", "medium", f"No circuit breaker to {label(e['target'])}",
                               "Third-party/LLM dependencies have variable latency and rate limits.",
                               [label(e["target"])], "Wrap with circuit breaker + fallback route.", "no_timeout"))
    queues = [i for i, n in nodes.items() if n.get("kind") == "queue"]
    for q in queues:
        if not nodes[q].get("dlq"):
            findings.append(_f("reliability", "low", f"Queue without dead-letter queue: {label(q)}",
                               "Poison messages will block or be lost.", [label(q)], "Configure a DLQ with alerting."))

    # scalability: shared database / missing cache
    for i, n in nodes.items():
        if n.get("kind") == "database":
            writers = {e["source"] for e in in_e[i] if nodes[e["source"]].get("kind") in ("service", "worker")}
            if len(writers) >= 3:
                findings.append(_f("scalability", "medium", f"Shared database: {label(i)}",
                                   f"{len(writers)} services couple through one schema.", [label(i)],
                                   "Assign ownership per service; expose data via APIs/events.", "shared_db"))
            if len(in_e[i]) >= 2 and not any(nodes[e["source"]].get("kind") == "cache" for e in in_e[i]) \
                    and not any(m.get("kind") == "cache" for m in nodes.values()):
                findings.append(_f("scalability", "low", f"No cache tier in front of {label(i)}",
                                   "Read-heavy paths will scale the database vertically.", [label(i)],
                                   "Add a read-through cache for hot keys.", "no_cache"))

    # security
    for i, n in nodes.items():
        if n.get("public") and not n.get("auth"):
            findings.append(_f("security", "critical", f"Public endpoint without authentication: {label(i)}",
                               "Internet-exposed component accepts unauthenticated traffic.", [label(i)],
                               "Require OIDC/JWT at the edge; add rate limiting and WAF.", "public_no_auth"))
        if n.get("kind") in STATEFUL_KINDS and n.get("encrypted") is False:
            findings.append(_f("security", "high", f"Data store not encrypted at rest: {label(i)}", "", [label(i)],
                               "Enable storage encryption with managed keys (KMS)."))
        if n.get("kind") == "mcp_server" and not n.get("auth"):
            findings.append(_f("security", "high", f"MCP server without authentication: {label(i)}",
                               "Tool servers can perform side effects; unauthenticated access enables abuse.", [label(i)],
                               "Require API key/OAuth and enforce tool allowlists via a policy engine."))
    for e in edges:
        s, t = nodes[e["source"]], nodes[e["target"]]
        if s.get("kind") == "llm" and t.get("kind") in ("database", "vector_db"):
            findings.append(_f("security", "high", f"LLM directly accesses data store {label(e['target'])}",
                               "Model-driven queries bypass authorization and risk data exfiltration via prompt injection.",
                               [label(e["source"]), label(e["target"])], "Mediate via a service enforcing tenant + RBAC filters.",
                               "llm_direct_data"))
        if t.get("kind") == "external" and not e.get("auth"):
            findings.append(_f("security", "medium", f"Unauthenticated call to external {label(e['target'])}", "",
                               [label(e["target"])], "Use scoped credentials from a secret manager."))

    # maintainability / operational complexity
    degree = {i: len(in_e[i]) + len(out_e[i]) for i in nodes}
    for i, d in degree.items():
        if d >= 7 and nodes[i].get("kind") == "service":
            findings.append(_f("maintainability", "medium", f"High coupling hub: {label(i)} ({d} connections)",
                               "God-service risk: changes ripple widely.", [label(i)], "Split by bounded context."))
    services = [i for i, n in nodes.items() if n.get("kind") in ("service", "worker")]
    if len(services) > 8 and req.get("team_count", 3) <= 2:
        findings.append(_f("operational_complexity", "medium", "More services than the team can operate",
                           f"{len(services)} services for {req.get('team_count', 'few')} team(s).", [],
                           "Prefer a modular monolith until team/scale requires extraction."))
    unobserved = [label(i) for i, n in nodes.items() if n.get("kind") in ("service", "gateway", "worker") and n.get("observability") is False]
    if unobserved:
        findings.append(_f("operational_complexity", "medium", "Components without observability", ", ".join(unobserved),
                           unobserved, "Emit OTel traces + RED metrics; define SLOs.", "no_observability"))

    # cost
    monthly = sum(MONTHLY_COST.get(n.get("kind", "service"), 70) * max(1, int(n.get("replicas", 1) or 1)) for n in nodes.values())
    llms = [label(i) for i, n in nodes.items() if n.get("kind") == "llm"]
    if llms:
        findings.append(_f("cost", "low", "LLM spend dominates variable cost",
                           "Token cost scales with traffic; add caching, routing to smaller models and budgets.", llms,
                           "Introduce semantic caching and model routing with per-tenant quotas."))
    if req.get("monthly_budget_usd") and monthly > req["monthly_budget_usd"]:
        findings.append(_f("cost", "high", "Estimated cost exceeds budget",
                           f"~${monthly:,.0f}/mo vs budget ${req['monthly_budget_usd']:,.0f}.", [],
                           "Right-size replicas; use managed serverless tiers for spiky workloads."))

    scores = {c: 100 for c in CATEGORIES}
    for f in findings:
        scores[f["category"]] = max(0, scores[f["category"]] - SEVERITY_PENALTY[f["severity"]])
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: order[f["severity"]])
    tradeoffs = []
    for key in dict.fromkeys(f["tradeoff_key"] for f in findings if f["tradeoff_key"]):
        related = [f["title"] for f in findings if f["tradeoff_key"] == key]
        tradeoffs.append({"concern": key, "findings": related, "alternatives": TRADEOFFS[key]})
    return {
        "method": "deterministic-rules-v1",
        "scores": scores,
        "overall": round(sum(scores.values()) / len(scores), 1),
        "findings": findings,
        "tradeoffs": tradeoffs,
        "metrics": {"components": len(nodes), "connections": len(edges), "services": len(services),
                    "max_sync_depth": depth, "critical_path": [label(p) for p in path],
                    "critical_path_latency_ms": round(lat, 1), "estimated_monthly_cost_usd": monthly,
                    "cost_note": "rough list-price estimate per component kind x replicas"},
    }


def sequence_from_graph(graph: dict[str, Any]) -> str:
    """Generate a Mermaid sequence diagram skeleton from synchronous edges (BFS from clients)."""
    nodes = {n["id"]: (n.get("data") or {}).get("label", n["id"]) for n in graph.get("nodes", [])}
    lines = ["sequenceDiagram"]
    for e in graph.get("edges", []):
        d = e.get("data") or {}
        a, b = nodes.get(e["source"], e["source"]), nodes.get(e["target"], e["target"])
        arrow = "-)" if d.get("protocol") in ("queue", "event", "async") else "->>"
        lines.append(f"    {a.replace(' ', '_')}{arrow}{b.replace(' ', '_')}: {d.get('protocol', 'call')}")
    return "\n".join(lines)
