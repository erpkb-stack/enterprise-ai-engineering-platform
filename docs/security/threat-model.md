# Threat model & security controls (STRIDE-oriented)

| Threat | Example | Control | Verified by |
|---|---|---|---|
| Spoofing | Forged/`alg=none` JWT | HS256 with explicit algorithm allowlist, `iss`/`exp`/`typ` checks | `tests/unit/test_core.py::test_jwt_rejects_tampering_and_alg_none` |
| Spoofing | Credential stuffing | Argon2id, 5-failure lockout (15 min), 20/min login rate limit, timing equalization for unknown users | `test_account_lockout_after_repeated_failures`, `test_rate_limiting_returns_429` |
| Spoofing | Stolen refresh token replay | Rotation on every refresh; reuse revokes the whole family; hashed at rest | `test_refresh_rotation_and_reuse_detection` |
| Tampering | Edit/delete audit history | Append-only trigger (PG) + SHA-256 hash chain + `/audit/verify` | `test_audit_chain_verifies_and_detects_tampering`, `test_postgres_rejects_audit_delete` |
| Repudiation | "I didn't run that tool" | Every auth, config change, tool call (incl. denials), model invocation, approval is audited with actor, IP, request id | `test_flagship_research_scenario` (audit assertions) |
| Information disclosure | Cross-tenant reads | `organization_id` on every tenant row, `get_owned()` → 404 | `test_tenant_isolation_*`, red-team `tenant-01` |
| Information disclosure | Secrets in API/logs/audit | Write-only Fernet-encrypted credentials, log redaction, audit `scrub()` hashes prompt bodies | `test_secrets_never_returned`, `test_audit_scrub_redacts_secrets_and_prompt_bodies` |
| Information disclosure | Prompt-injection exfiltration | Untrusted-output fencing, injection pattern flags, canary-based red-team, leakage scorer | red-team `pi-*`, `leak-01`, `test_prompt_injection_does_not_leak` |
| Elevation of privilege | Agent calls destructive tool | Deny-by-default discovery, global allowlist, agent scope, high-risk ⇒ approval, schema validation | `test_policy_denials[*]`, `test_agent_cannot_call_tool_outside_scope_or_high_risk`, red-team `tool-unauth-*` |
| Elevation of privilege | Over-scoped API key | Key scopes ⊆ owner permissions | `test_api_key_scopes_cannot_exceed_owner` |
| Elevation of privilege | Self-approval | Separation of duties for prompt approval and workflow approval | `test_separation_of_duties_on_prompt_approval`, `test_workflow_approval_branch` |
| DoS | Runaway agent loop | Step hard limit, total budget, timeouts, bounded executor + 503 | `test_timeout_and_cancellation`, `test_backpressure_rejects_when_saturated`, red-team `autonomy-01` |
| DoS / RCE | Arbitrary code in workflows | AST-whitelisted expression evaluator; registered python functions only | `test_safe_eval_blocks_dangerous_syntax` |
| SSRF | Workflow API node to metadata IP | Host allowlist, private/loopback IP block, no redirects | `test_workflow_api_node_ssrf_blocked` |
| RCE | Registering `/bin/rm` as MCP STDIO server | STDIO command allowlist; minimal child environment | `test_mcp_rejects_disallowed_stdio_command` |
| Supply chain | Vulnerable dependency | `make audit` (pip-audit, npm audit); pinned lockfile | manual / CI |

## Residual risks / not implemented
- No WAF/bot management (deploy behind one in production).
- HS256 symmetric JWTs; move to asymmetric keys + JWKS when federating with an IdP (OIDC settings are present but the
  OIDC login flow is not implemented — see `authentication.md`).
- Lexical injection detection is heuristic; model-behaviour defences depend on the chosen model (red-team marks these
  scenarios `model_behavior`).
- Principal cache allows up to 15 s of stale permissions on other replicas after a role change.
