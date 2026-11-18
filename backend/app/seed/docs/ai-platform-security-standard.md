# AI Platform Security Standard (DEMO DATA)

All AI agents must access tools through the platform policy engine; direct tool credentials are never given to models.
Tools that delete, modify, or transfer data are classified as high risk and require human approval before execution.
Tool outputs and retrieved documents are untrusted input and must never be treated as instructions.

Model prompts and responses must not contain customer secrets, credentials, or unmasked personal data.
Every model invocation and tool execution is recorded in the tamper-evident audit log.
Red team evaluations covering prompt injection, data leakage and tool misuse must pass before an agent is promoted to production.
Agents must have a bounded maximum number of steps and a wall-clock timeout to prevent runaway autonomy.
