# Design — Google Login Gate for BL Auditor

**Date:** 2026-07-02
**Status:** Approved (design), pending implementation plan

## Goal

Add Google (OpenID Connect) login to the BL Auditor FastAPI app. Nothing is
accessible without a valid, logged-in session. Only Google accounts whose email
ends in an allowed domain (`@indiamart.com`, `@intermesh.net`) may sign in. The
existing RabbitMQ consumer must keep working with no auth changes.

## Non-goals

- No user database, roles, or per-user permissions (single access tier).
- No server-side session store (signed cookie only).
- No changes to the consumer code or its config.
- No reverse-proxy / oauth2-proxy layer — auth lives in the app.

## Approach

In-app authentication using **Authlib** (Google OIDC) + Starlette
**SessionMiddleware** (signed cookie). Fits the existing no-DB / env-var / CSV
style. Two new dependencies: `authlib`, `itsdangerous`.

### Flow

```
unauth browser → any gated route
  → 302 /login  (branded page with "Sign in with Google")
  → GET /auth/login   → redirect to Google consent (Authlib handles state/CSRF)
  → GET /auth/callback
      validate: id_token.email_verified == true AND email domain ∈ allowlist
        ok     → store {"email": ...} in signed session cookie → 302 to original URL
        not ok → clear session → 403 access-denied page
```

## Components

New module `app/auth/`:

| File | Purpose |
|------|---------|
| `app/auth/__init__.py` | Package marker; exports router + middleware factory |
| `app/auth/oauth.py` | Register Google OAuth client via OIDC discovery URL |
| `app/auth/config.py` | Read/parse env vars (domains, CIDRs, secrets, redirect base) |
| `app/auth/routes.py` | `/login`, `/auth/login`, `/auth/callback`, `/auth/logout` |
| `app/auth/middleware.py` | Enforcement middleware (exempt-path + session check) |

Edits:

| File | Change |
|------|--------|
| `main.py` | Add `SessionMiddleware`, add auth middleware, include auth router, gate `/docs` `/openapi.json` |
| `app/templates/login.html` | New — branded "Sign in with Google" page (extends `base.html`) |
| `app/templates/access_denied.html` | New — "use your @indiamart.com / @intermesh.net account" (extends `base.html`) |
| `app/templates/base.html` | Show signed-in email + Logout link in navbar |
| `requirements.txt` | Add `authlib`, `itsdangerous` |
| `CLAUDE.md` / `README.md` | Document new env vars |

## Enforcement rules (middleware)

Order: exempt checks first, then session check.

- **Exempt for everyone:** `/static/*`, `/login`, `/auth/login`,
  `/auth/callback`, `/auth/logout`, `/health` (new trivial endpoint).
- **Exempt for localhost/internal only:** `POST /audit` (the consumer's
  ingestion path). Exemption granted only when the **real TCP peer**
  (`request.client.host`) is inside a configured trusted CIDR list.
  **`X-Forwarded-For` is NOT trusted** for this decision, so external callers
  cannot spoof localhost. Non-localhost requests to `/audit` still require a
  session (the human dashboard form submits from an already-authenticated
  browser, so it is unaffected).
- **Everything else requires a valid session**, including `/`, `/activity`,
  `/records`, `/traces*`, `/prompts*`, `/batch*`, `/admin_view/*`, `/api/audit`,
  `/download/*`, `/demo`, `/clear-logs`, `/docs`, `/openapi.json`.
  - Browser / HTML request without session → `302 /login` (store the requested
    URL as `next` so we return there post-login).
  - `/api/*` or XHR (JSON `Accept` / `X-Requested-With`) without session →
    `401` JSON, no redirect.

## Domain validation

After Google returns the ID token / userinfo:

1. Require `email_verified == true`.
2. Require the email's domain to be in the allowlist.

Allowlist comes from env `ALLOWED_EMAIL_DOMAINS` (comma-separated), so adding a
domain needs no code change. Because two domains are allowed, we match the email
suffix ourselves — Google's `hd` (hosted-domain) claim only covers a single
Workspace domain and is not sufficient.

Failure → clear any partial session, render `access_denied.html` with HTTP 403.

## Session

- Starlette `SessionMiddleware`, signed with `SESSION_SECRET`.
- Cookie flags: `HttpOnly` (default), `SameSite=Lax`, `Secure` in production
  (driven by env / redirect base scheme).
- `max_age = SESSION_MAX_AGE` (default 604800 = 7 days). Expiry forces re-login.
- No server-side revocation (acceptable for an internal single-tier tool);
  rotating `SESSION_SECRET` invalidates all sessions if ever needed.

## New environment variables

```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
SESSION_SECRET=...                          # signs the session cookie (required)
ALLOWED_EMAIL_DOMAINS=indiamart.com,intermesh.net
OAUTH_REDIRECT_BASE_URL=https://<host>      # builds the callback URL
TRUSTED_INGEST_CIDRS=127.0.0.1/32,::1/128   # localhost exemption for POST /audit
SESSION_MAX_AGE=604800                      # 7 days
```

Startup should fail loud if `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, or
`SESSION_SECRET` are missing (per CLAUDE.md Rule 12), rather than silently
running without auth.

## Manual prerequisite (one-time)

Create an OAuth 2.0 Client ID in Google Cloud Console:
- Application type: Web application.
- Authorized redirect URI: `<OAUTH_REDIRECT_BASE_URL>/auth/callback`.
- Put the client ID/secret into the env vars above.

## Error handling

- Missing required env at startup → raise, app refuses to boot.
- OAuth error / user denies consent at Google → back to `/login` with a message.
- Verified-but-disallowed domain → 403 `access_denied.html`.
- Expired/invalid session cookie → treated as unauthenticated → `/login`.

## Testing / verification

- Manual: unauth request to `/`, `/records`, `/download/*`, `/docs` all bounce
  to `/login`; after Google login with an allowed domain they load.
- Manual: login with a non-allowed domain → 403 access-denied.
- Manual: `POST /audit` from localhost (curl on the host) succeeds without a
  session; the same POST from a remote IP without a session is rejected.
- Manual: consumer end-to-end still audits offers with no config change.
- Manual: `X-Forwarded-For: 127.0.0.1` from a remote IP does NOT bypass the gate.

## Open items to confirm during planning

- Exact production `OAUTH_REDIRECT_BASE_URL` and whether nginx terminates TLS
  (affects `Secure` cookie + whether the consumer reaches uvicorn directly on
  localhost or via nginx — determines the correct `TRUSTED_INGEST_CIDRS`).
