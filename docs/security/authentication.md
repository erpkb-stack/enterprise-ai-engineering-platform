# Authentication & authorization

## Tokens
- `POST /api/v1/auth/login` → `access_token` (JWT HS256, 15 min, claims `sub, org, roles, iss, iat, nbf, exp, jti, typ=access`)
  and an opaque `refresh_token` (32 bytes, stored as SHA-256).
- `POST /api/v1/auth/refresh` rotates the refresh token. Presenting an already-rotated token revokes the entire session
  family and writes a `critical` audit event (`auth.refresh_reuse_detected`).
- `POST /api/v1/auth/logout` revokes the family. `GET/DELETE /api/v1/auth/sessions` list/revoke sessions.
- The SPA keeps the access token in memory and the refresh token in `sessionStorage` (cleared when the tab closes).
  Trade-off: `sessionStorage` is readable by XSS; a strict CSP (see nginx config) and no third-party scripts mitigate.
  An httpOnly cookie + CSRF token design is the next hardening step.

## API keys
`X-API-Key: aiecc_…` — created in Security & Audit, shown once, stored hashed, optional expiry, scopes intersected with
the owner's live permissions.

## RBAC
Seven system roles (Platform Administrator, Engineering Administrator, AI Engineer, Developer, Security Reviewer,
Auditor, Read-Only User) over 36 permissions (`app/security/rbac.py`). Endpoints declare required permissions with
`Depends(require("perm"))`. The SPA hides actions the user cannot perform, but the server is authoritative.

## OIDC / OAuth 2.0 (integration-ready)
`AIECC_OIDC_ISSUER_URL` / `AIECC_OIDC_CLIENT_ID` are reserved. To integrate: implement an authorization-code + PKCE
callback that maps IdP groups to AIECC roles, then issue AIECC tokens (or validate IdP JWTs via JWKS in
`decode_access_token`). Not implemented in this release; password auth is the working path.

MCP servers support OAuth 2.0 **client credentials** today (`auth_type=oauth2`).
