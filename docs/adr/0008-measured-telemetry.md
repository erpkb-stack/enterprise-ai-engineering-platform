# ADR-0008: Measured telemetry only; synthetic traffic is tagged

- Status: Accepted

## Decision
All dashboard metrics come from persisted records (executions, usage_metrics, audit) or from an in-process rolling
window fed by real requests; nothing is hardcoded. Prometheus histograms are exported for Grafana; OTel spans wrap
LLM, tool, agent, workflow and retrieval operations. The built-in load runner tags its requests with a server-derived
token (`X-AIECC-Synthetic`) so synthetic and organic traffic are always separable (clients cannot spoof it).

## Consequences
+ Dashboards are trustworthy and reproducible.
− The rolling window is per-process; multi-replica views come from Prometheus aggregation.
