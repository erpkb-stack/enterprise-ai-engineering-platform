# ADR-0007: Security model — RBAC + policy engine + tamper-evident audit

- Status: Accepted

## Decision
- AuthN: Argon2id passwords (off the event loop), 15-min HS256 JWT access tokens with explicit algorithm allowlist,
  rotating refresh tokens stored hashed with reuse detection (family revocation), account lockout, OIDC-ready config.
- AuthZ: 7 roles over 36 `resource:action` permissions; API keys can only narrow the owner's permissions.
- Tool authorization: a policy engine (allowlist → server enabled → role restriction → caller permission → agent
  scope → approval for high-risk → JSON-schema validation). Deny wins; every decision is recorded.
- Tenancy: every tenant row carries `organization_id`; lookups use `get_owned()` which returns 404 for foreign rows.
- Audit: append-only table (PostgreSQL trigger blocks UPDATE/DELETE/TRUNCATE) with a per-org SHA-256 hash chain;
  `/audit/verify` detects tampering. Secrets are redacted and prompt bodies hashed before storage.
- Secrets at rest: integration credentials encrypted with Fernet; write-only in the API.

## Consequences
+ Defence in depth; each control has automated tests (tests/security).
− HS256 shared secret is simple but means every API replica can mint tokens; move to RS256/JWKS when an external
  IdP issues tokens. Principal cache (15 s) delays role-revocation across replicas by up to 15 s.
