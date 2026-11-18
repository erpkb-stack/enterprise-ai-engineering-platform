# ADR-0009: Resumable DAG workflow engine with dead-path elimination

- Status: Accepted

## Decision
Workflows are validated DAGs (one input, ≥1 output, no cycles, typed branch handles). Execution state (queue, edge
states, node outputs) is persisted after every node so human-approval and breakpoint pauses survive restarts and span
requests. Branch merges use dead-path elimination (a node runs when all incoming edges are resolved and at least one
was taken). Failures route to `error` handles; otherwise the workflow fails. "Python function" nodes call a registry of
vetted functions — never arbitrary code. API nodes enforce a hostname allowlist and block private/loopback IPs (SSRF).
Resumed executions run with the initiator's authority; approvers only authorize the step.

## Consequences
+ Human-in-the-loop and step debugging without long-lived processes.
− No parallel branch execution yet (nodes run sequentially); acceptable for current workloads.
