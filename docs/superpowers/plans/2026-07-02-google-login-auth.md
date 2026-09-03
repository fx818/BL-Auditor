# Google Login Auth Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require Google (OIDC) login for all access to the BL Auditor app, restricted to `@indiamart.com` / `@intermesh.net` emails, while the RabbitMQ consumer keeps hitting `POST /audit` with no auth.

**Architecture:** In-app auth. Authlib drives the Google OpenID Connect flow; Starlette `SessionMiddleware` holds a signed-cookie session (no DB). A custom `AuthMiddleware` gates every request: exempt paths pass, `POST /audit` passes only from a trusted peer IP, everything else needs a valid session. The allow/exempt/trust decisions live in pure functions so they are unit-tested; the OAuth round-trip is verified manually.

**Tech Stack:** FastAPI, Starlette middleware, Authlib (Google OIDC), itsdangerous (cookie signing), Jinja2, pytest (new — for pure-logic tests only).

## Global Constraints

- Python deps managed in `requirements.txt` (single file, no dev/prod split).
- New deps: `authlib`, `itsdangerous`, `pytest>=8.0`.
- No database — session state is a signed cookie only.
- Fail loud at startup if `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, or `SESSION_SECRET` are unset (CLAUDE.md Rule 12).
- Env-driven config; adding an allowed domain must need no code change.
- `X-Forwarded-For` must NOT be trusted for the `/audit` IP exemption — use the real TCP peer (`request.client.host`) only.
- Allowed domains default: `indiamart.com,intermesh.net`. Trusted CIDRs default: `127.0.0.1/32,::1/128`.
- Match existing style: `os.environ.get(...)` with defaults, `load_dotenv()` already called in `main.py`.

## File Structure

- Create `app/auth/__init__.py` — exports `oauth`, `AuthMiddleware`, `router`, `settings`.
- Create `app/auth/config.py` — `AuthSettings` loader + pure decision functions.
- Create `app/auth/oauth.py` — Authlib Google client registration.
- Create `app/auth/routes.py` — `/login`, `/auth/login`, `/auth/callback`, `/auth/logout`.
- Create `app/auth/middleware.py` — `AuthMiddleware` (wires the pure functions).
- Create `app/templates/login.html`, `app/templates/access_denied.html` — standalone pre-auth pages (do NOT extend `base.html`, whose nav points at gated routes).
- Modify `main.py` — add both middlewares (correct order), include router, gate docs, add `/health`, startup env validation.
- Modify `app/templates/base.html` — show signed-in email + Logout link.
- Modify `requirements.txt` — add deps.
- Create `tests/__init__.py`, `tests/test_auth_config.py` — unit tests for pure logic.
- Modify `README.md` and `CLAUDE.md` — document new env vars + deployment note.

---

### Task 1: Dependencies + pure config/decision logic

**Files:**
- Modify: `requirements.txt`
- Create: `app/auth/__init__.py` (empty placeholder for now; filled in Task 5)
- Create: `app/auth/config.py`
- Create: `tests/__init__.py`
- Test: `tests/test_auth_config.py`

**Interfaces:**
- Produces:
  - `AuthSettings` dataclass with fields: `google_client_id: str`, `google_client_secret: str`, `session_secret: str`, `allowed_domains: frozenset[str]`, `trusted_cidrs: tuple`, `redirect_base: str`, `session_max_age: int`, `https_only: bool`.
  - `load_settings() -> AuthSettings` (raises `RuntimeError` on missing required vars).
  - `is_allowed_email(email: str | None, verified: bool, allowed_domains: frozenset[str]) -> bool`
  - `is_trusted_peer(host: str | None, cidrs: tuple) -> bool`
  - `classify_request(method: str, path: str) -> str` returning `"public"`, `"trusted_only"`, or `"gated"`.

- [ ] **Step 1: Add dependencies**

Append to `requirements.txt`:

```
authlib>=1.3.0
itsdangerous>=2.2.0
pytest>=8.0
```

- [ ] **Step 2: Write failing tests**

Create `tests/__init__.py` (empty). Create `tests/test_auth_config.py`:

```python
import ipaddress
import pytest
from app.auth.config import (
    is_allowed_email,
    is_trusted_peer,
    classify_request,
)

DOMAINS = frozenset({"indiamart.com", "intermesh.net"})
CIDRS = (ipaddress.ip_network("127.0.0.1/32"), ipaddress.ip_network("::1/128"))


class TestIsAllowedEmail:
    def test_allowed_domain_verified(self):
        assert is_allowed_email("a.b@indiamart.com", True, DOMAINS) is True

    def test_second_allowed_domain(self):
        assert is_allowed_email("x@intermesh.net", True, DOMAINS) is True

    def test_case_insensitive_domain(self):
        assert is_allowed_email("x@IndiaMart.com", True, DOMAINS) is True

    def test_unverified_rejected(self):
        # WHY: an unverified Google email can be attacker-controlled; must never pass.
        assert is_allowed_email("x@indiamart.com", False, DOMAINS) is False

    def test_wrong_domain_rejected(self):
        assert is_allowed_email("x@gmail.com", True, DOMAINS) is False

    def test_lookalike_domain_rejected(self):
        # WHY: substring/suffix tricks like "evilindiamart.com" must not match.
        assert is_allowed_email("x@evilindiamart.com", True, DOMAINS) is False

    def test_none_or_malformed_rejected(self):
        assert is_allowed_email(None, True, DOMAINS) is False
        assert is_allowed_email("noatsign", True, DOMAINS) is False


class TestIsTrustedPeer:
    def test_localhost_v4_trusted(self):
        assert is_trusted_peer("127.0.0.1", CIDRS) is True

    def test_localhost_v6_trusted(self):
        assert is_trusted_peer("::1", CIDRS) is True

    def test_external_ip_untrusted(self):
        assert is_trusted_peer("203.0.113.5", CIDRS) is False

    def test_none_untrusted(self):
        assert is_trusted_peer(None, CIDRS) is False

    def test_garbage_untrusted(self):
        assert is_trusted_peer("not-an-ip", CIDRS) is False


class TestClassifyRequest:
    def test_login_public(self):
        assert classify_request("GET", "/login") == "public"

    def test_auth_routes_public(self):
        assert classify_request("GET", "/auth/callback") == "public"

    def test_static_public(self):
        assert classify_request("GET", "/static/css/style.css") == "public"

    def test_health_public(self):
        assert classify_request("GET", "/health") == "public"

    def test_audit_post_trusted_only(self):
        assert classify_request("POST", "/audit") == "trusted_only"

    def test_audit_get_is_gated(self):
        # WHY: only the consumer's POST ingestion is exempt; nothing else on /audit.
        assert classify_request("GET", "/audit") == "gated"

    def test_admin_audit_gated(self):
        assert classify_request("POST", "/admin_view/audit") == "gated"

    def test_api_audit_gated(self):
        assert classify_request("POST", "/api/audit") == "gated"

    def test_root_gated(self):
        assert classify_request("GET", "/") == "gated"

    def test_docs_gated(self):
        assert classify_request("GET", "/docs") == "gated"

    def test_download_gated(self):
        assert classify_request("GET", "/download/audit-traces") == "gated"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_auth_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth.config'` (or import error).

- [ ] **Step 4: Implement `app/auth/config.py`**

```python
import ipaddress
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AuthSettings:
    google_client_id: str
    google_client_secret: str
    session_secret: str
    allowed_domains: frozenset
    trusted_cidrs: tuple
    redirect_base: str
    session_max_age: int
    https_only: bool


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


def _parse_domains(raw: str) -> frozenset:
    return frozenset(d.strip().lower() for d in raw.split(",") if d.strip())


def _parse_cidrs(raw: str) -> tuple:
    nets = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return tuple(nets)


def load_settings() -> AuthSettings:
    redirect_base = os.environ.get("OAUTH_REDIRECT_BASE_URL", "").rstrip("/")
    return AuthSettings(
        google_client_id=_require("GOOGLE_CLIENT_ID"),
        google_client_secret=_require("GOOGLE_CLIENT_SECRET"),
        session_secret=_require("SESSION_SECRET"),
        allowed_domains=_parse_domains(
            os.environ.get("ALLOWED_EMAIL_DOMAINS", "indiamart.com,intermesh.net")
        ),
        trusted_cidrs=_parse_cidrs(
            os.environ.get("TRUSTED_INGEST_CIDRS", "127.0.0.1/32,::1/128")
        ),
        redirect_base=redirect_base,
        session_max_age=int(os.environ.get("SESSION_MAX_AGE", "604800")),
        https_only=redirect_base.startswith("https://"),
    )


def is_allowed_email(email, verified, allowed_domains) -> bool:
    if not verified or not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].lower()
    return domain in allowed_domains


def is_trusted_peer(host, cidrs) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in cidrs)


def classify_request(method: str, path: str) -> str:
    if (
        path == "/login"
        or path == "/health"
        or path.startswith("/auth/")
        or path.startswith("/static/")
    ):
        return "public"
    if method == "POST" and path == "/audit":
        return "trusted_only"
    return "gated"
```

- [ ] **Step 5: Create empty `app/auth/__init__.py`**

```python
# app/auth package — auth wiring is added in later tasks.
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_auth_config.py -v`
Expected: PASS — all tests green.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt app/auth/__init__.py app/auth/config.py tests/__init__.py tests/test_auth_config.py
git commit -m "feat(auth): pure config + allow/exempt/trust decision logic with tests"
```

---

### Task 2: Google OAuth client registration

**Files:**
- Create: `app/auth/oauth.py`

**Interfaces:**
- Consumes: `AuthSettings` from Task 1.
- Produces: `build_oauth(settings: AuthSettings)` returning an Authlib `OAuth` instance with a registered `google` client (accessed as `oauth.google`).

- [ ] **Step 1: Implement `app/auth/oauth.py`**

```python
from authlib.integrations.starlette_client import OAuth

GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"


def build_oauth(settings) -> OAuth:
    oauth = OAuth()
    oauth.register(
        name="google",
        server_metadata_url=GOOGLE_METADATA_URL,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth
```

- [ ] **Step 2: Verify it imports**

Run: `python -c "from app.auth.oauth import build_oauth; print('ok')"`
Expected: prints `ok` (no import error). This needs `authlib` installed: `pip install -r requirements.txt` first.

- [ ] **Step 3: Commit**

```bash
git add app/auth/oauth.py
git commit -m "feat(auth): register Google OIDC client via Authlib"
```

---

### Task 3: Auth routes + pre-auth templates

**Files:**
- Create: `app/auth/routes.py`
- Create: `app/templates/login.html`
- Create: `app/templates/access_denied.html`

**Interfaces:**
- Consumes: `oauth` (Task 2), `settings` + `is_allowed_email` (Task 1). These are passed in from `main.py` wiring (Task 5) via module-level setters to avoid circular imports.
- Produces:
  - `router: APIRouter` with routes: `GET /login`, `GET /auth/login`, `GET /auth/callback`, `GET /auth/logout`.
  - `init_routes(oauth, settings)` — called once at startup to inject dependencies.

- [ ] **Step 1: Implement `app/auth/routes.py`**

```python
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.config import is_allowed_email

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_oauth = None
_settings = None


def init_routes(oauth, settings):
    global _oauth, _settings
    _oauth = oauth
    _settings = settings


@router.get("/login")
async def login_page(request: Request):
    # Already signed in? Go home.
    if request.session.get("user"):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {})


@router.get("/auth/login")
async def auth_login(request: Request):
    redirect_uri = f"{_settings.redirect_base}/auth/callback"
    return await _oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        token = await _oauth.google.authorize_access_token(request)
    except Exception:
        return RedirectResponse("/login", status_code=302)

    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email")
    verified = bool(userinfo.get("email_verified"))

    if not is_allowed_email(email, verified, _settings.allowed_domains):
        request.session.pop("user", None)
        return templates.TemplateResponse(
            request, "access_denied.html", {"email": email}, status_code=403
        )

    request.session["user"] = {"email": email}
    next_url = request.session.pop("next", "/") or "/"
    return RedirectResponse(next_url, status_code=302)


@router.get("/auth/logout")
async def auth_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)
```

- [ ] **Step 2: Create `app/templates/login.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Sign in - BL Auditor</title>
  <link rel="stylesheet" href="/static/css/style.css?v=light-3" />
</head>
<body>
  <div style="min-height:100vh;display:flex;align-items:center;justify-content:center;">
    <div style="text-align:center;padding:40px;border:1px solid var(--border,#333);border-radius:12px;">
      <div class="navbar-logo" style="margin:0 auto 16px;">BL</div>
      <h1 style="margin:0 0 8px;">BL Auditor</h1>
      <p style="color:var(--text-muted,#888);margin:0 0 24px;">
        Sign in with your IndiaMART account to continue.
      </p>
      <a href="/auth/login" class="btn btn-primary">Sign in with Google</a>
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 3: Create `app/templates/access_denied.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Access denied - BL Auditor</title>
  <link rel="stylesheet" href="/static/css/style.css?v=light-3" />
</head>
<body>
  <div style="min-height:100vh;display:flex;align-items:center;justify-content:center;">
    <div style="text-align:center;padding:40px;border:1px solid var(--border,#333);border-radius:12px;max-width:460px;">
      <h1 style="margin:0 0 8px;">Access denied</h1>
      <p style="color:var(--text-muted,#888);margin:0 0 8px;">
        {% if email %}<code>{{ email }}</code> is not permitted.{% endif %}
      </p>
      <p style="color:var(--text-muted,#888);margin:0 0 24px;">
        Only <b>@indiamart.com</b> and <b>@intermesh.net</b> accounts can access this platform.
      </p>
      <a href="/auth/logout" class="btn btn-outline">Try a different account</a>
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 4: Verify routes import**

Run: `python -c "from app.auth.routes import router, init_routes; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Commit**

```bash
git add app/auth/routes.py app/templates/login.html app/templates/access_denied.html
git commit -m "feat(auth): login/callback/logout routes + pre-auth pages"
```

---

### Task 4: Enforcement middleware

**Files:**
- Create: `app/auth/middleware.py`

**Interfaces:**
- Consumes: `classify_request`, `is_trusted_peer` (Task 1).
- Produces: `AuthMiddleware(BaseHTTPMiddleware)` — constructed as `AuthMiddleware(app, settings=settings)`. Redirects unauthenticated HTML requests to `/login` (storing `next` in session); returns `401` JSON for API/XHR requests; passes `trusted_only` paths when the peer IP is trusted.

- [ ] **Step 1: Implement `app/auth/middleware.py`**

```python
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from app.auth.config import classify_request, is_trusted_peer


def _wants_json(request) -> bool:
    if request.url.path.startswith("/api/"):
        return True
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings):
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request, call_next):
        decision = classify_request(request.method, request.url.path)

        if decision == "public":
            return await call_next(request)

        if decision == "trusted_only":
            peer = request.client.host if request.client else None
            if is_trusted_peer(peer, self._settings.trusted_cidrs):
                return await call_next(request)
            # not trusted -> fall through to session gate

        if request.session.get("user"):
            return await call_next(request)

        if _wants_json(request):
            return JSONResponse({"detail": "authentication required"}, status_code=401)

        request.session["next"] = request.url.path
        return RedirectResponse("/login", status_code=302)
```

- [ ] **Step 2: Verify it imports**

Run: `python -c "from app.auth.middleware import AuthMiddleware; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add app/auth/middleware.py
git commit -m "feat(auth): enforcement middleware (exempt/trusted/gated)"
```

---

### Task 5: Wire auth into the app

**Files:**
- Modify: `main.py`
- Modify: `app/auth/__init__.py`
- Modify: `app/templates/base.html`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: a booting app that enforces auth. `main.py` calls `load_settings()` (fail-loud), builds oauth, calls `init_routes`, adds middlewares in the correct order, includes the auth router, gates docs, adds `/health`.

**Middleware order is critical:** `AuthMiddleware` reads `request.session`, so `SessionMiddleware` must process the request first. In Starlette the **last** `add_middleware` call is the **outermost** (runs first on the request). Therefore add `AuthMiddleware` FIRST, then `SessionMiddleware`.

- [ ] **Step 1: Export from `app/auth/__init__.py`**

Replace the placeholder content with:

```python
from app.auth.config import load_settings
from app.auth.oauth import build_oauth
from app.auth.middleware import AuthMiddleware
from app.auth.routes import router, init_routes

__all__ = ["load_settings", "build_oauth", "AuthMiddleware", "router", "init_routes"]
```

- [ ] **Step 2: Rewrite `main.py`**

```python
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.routers import audit
from app.auth import (
    load_settings,
    build_oauth,
    AuthMiddleware,
    router as auth_router,
    init_routes,
)

# Fail loud if required auth env is missing.
auth_settings = load_settings()
oauth = build_oauth(auth_settings)
init_routes(oauth, auth_settings)

# Docs are gated by AuthMiddleware, but keep them on so signed-in users can use them.
app = FastAPI(
    title="BL Auditor",
    description="BuyLead Product Auditor Dashboard",
    version="2.0.0",
)

# Order matters: add AuthMiddleware first so SessionMiddleware ends up OUTERMOST
# (runs first, populating request.session before AuthMiddleware reads it).
app.add_middleware(AuthMiddleware, settings=auth_settings)
app.add_middleware(
    SessionMiddleware,
    secret_key=auth_settings.session_secret,
    max_age=auth_settings.session_max_age,
    same_site="lax",
    https_only=auth_settings.https_only,
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


app.include_router(auth_router)
app.include_router(audit.router)
```

- [ ] **Step 3: Add signed-in email + Logout to `app/templates/base.html`**

In `app/templates/base.html`, inside `<div class="navbar-actions">`, immediately before the `<span class="badge-live">Live</span>` line, insert:

```html
      {% if request is defined and request.session.get('user') %}
      <span class="btn btn-sm" style="pointer-events:none;">{{ request.session['user']['email'] }}</span>
      <a href="/auth/logout" class="btn btn-outline btn-sm">Logout</a>
      {% endif %}
```

- [ ] **Step 4: Boot the app and verify startup fails loud without env**

Run (no auth env set):
`python -c "import main"`
Expected: `RuntimeError: Missing required environment variable: GOOGLE_CLIENT_ID`.

- [ ] **Step 5: Boot with dummy env and confirm it imports**

Run:
```bash
GOOGLE_CLIENT_ID=x GOOGLE_CLIENT_SECRET=y SESSION_SECRET=z OAUTH_REDIRECT_BASE_URL=http://localhost:8080 python -c "import main; print('booted')"
```
(Windows PowerShell: set each with `$env:GOOGLE_CLIENT_ID='x'` etc. on one line, then `python -c "import main; print('booted')"`.)
Expected: prints `booted`.

- [ ] **Step 6: Commit**

```bash
git add main.py app/auth/__init__.py app/templates/base.html
git commit -m "feat(auth): wire session + auth middleware, gate app, add /health"
```

---

### Task 6: Manual end-to-end verification + docs

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:** none (verification + docs only).

- [ ] **Step 1: Create a real Google OAuth client (one-time, manual)**

In Google Cloud Console → APIs & Services → Credentials → Create OAuth client ID:
- Type: Web application.
- Authorized redirect URI: `<OAUTH_REDIRECT_BASE_URL>/auth/callback` (e.g. `http://localhost:8080/auth/callback` for local).
Put the client ID/secret into your `.env`.

- [ ] **Step 2: Run the app**

Set env (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `SESSION_SECRET`, `OAUTH_REDIRECT_BASE_URL=http://localhost:8080`) then:
`uvicorn main:app --reload --port 8080`

- [ ] **Step 3: Verify the gate (browser)**

- Visit `http://localhost:8080/` → redirected to `/login`.
- Visit `/records`, `/download/audit-traces`, `/docs` → each redirects to `/login`.
- Click "Sign in with Google", sign in with an `@indiamart.com` / `@intermesh.net` account → lands back on the originally requested page; navbar shows your email + Logout.
- Sign in with a `@gmail.com` account → 403 access-denied page.
- Click Logout → back to `/login`; revisiting `/` redirects to `/login` again.

- [ ] **Step 4: Verify the consumer exemption (localhost)**

From the same host, unauthenticated:
`curl -i -X POST http://localhost:8080/audit -H "Content-Type: application/json" -d "{\"offer_id\":\"142764424452\"}"`
Expected: NOT a redirect to `/login` — the audit runs (200 / normal audit response).

- [ ] **Step 5: Verify spoofing is blocked**

`curl -i -X POST http://localhost:8080/audit -H "X-Forwarded-For: 127.0.0.1" -H "Content-Type: application/json" -d "{\"offer_id\":\"1\"}"` **from a non-localhost client** must be rejected (302 `/login` or 401). Locally this is hard to prove; assert it in staging from a remote box. The point: XFF must never grant the exemption.

- [ ] **Step 6: PROXY SAFETY GATE (do not ship until this passes)**

If nginx (or any reverse proxy) runs on the **same host** and proxies browser traffic to uvicorn, proxied requests reach uvicorn as `127.0.0.1` and would wrongly satisfy the `/audit` IP exemption — letting unauthenticated users POST `/audit` through the public URL.

Verify from an external machine (not the app host), unauthenticated:
`curl -i -X POST https://<public-host>/audit -d "{\"offer_id\":\"1\"}"`
Expected: 302 `/login` or 401 — **rejected**.

If it is NOT rejected, you MUST do one of the following before shipping:
- Block `POST /audit` at the proxy (nginx `location = /audit { return 403; }`) so only the consumer, hitting uvicorn's internal port directly, can reach it; OR
- Point the consumer at uvicorn's internal address on a port the proxy does not expose, and set `TRUSTED_INGEST_CIDRS` to that path only.

Record which mitigation was applied in the deployment notes.

- [ ] **Step 7: Verify the consumer still audits end-to-end**

Run the consumer against a test queue (or invoke its audit call path) and confirm offers are audited with no consumer config change.

- [ ] **Step 8: Document env vars in `README.md` and `CLAUDE.md`**

Add to the environment section of both files:

```
# Google login (required — app refuses to boot without these three)
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
SESSION_SECRET=...                          # random secret; signs the session cookie
ALLOWED_EMAIL_DOMAINS=indiamart.com,intermesh.net
OAUTH_REDIRECT_BASE_URL=https://<host>      # builds the /auth/callback URL
TRUSTED_INGEST_CIDRS=127.0.0.1/32,::1/128   # peers allowed to POST /audit without login
SESSION_MAX_AGE=604800                      # session lifetime seconds (7 days)
```

Add a one-line note: "All routes require Google login except `/login`, `/auth/*`, `/static/*`, `/health`, and `POST /audit` from a trusted peer IP (the consumer). See the proxy-safety note if a same-host reverse proxy fronts the app."

- [ ] **Step 9: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs(auth): document Google login env vars + proxy-safety note"
```

---

## Self-Review

**Spec coverage:**
- In-app Authlib + SessionMiddleware → Tasks 2, 5. ✓
- Branded `/login` page → Task 3. ✓
- Domain allowlist + `email_verified` → Task 1 (`is_allowed_email`), Task 3 (callback). ✓
- Access-denied 403 page → Task 3. ✓
- Everything gated incl. `/docs`, `/download/*`, `/admin_view/*` → Task 1 (`classify_request` default `gated`), Task 5 (docs on but gated). ✓
- `POST /audit` exempt for trusted peer, XFF not trusted → Task 1 + Task 4. ✓
- Env vars + fail-loud startup → Task 1 (`load_settings`), Task 5. ✓
- Session cookie flags (HttpOnly/SameSite/Secure/max_age) → Task 5. ✓
- Manual verification incl. consumer end-to-end + proxy risk → Task 6. ✓
- Deps documented → Task 1 + Task 6. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✓

**Type consistency:** `AuthSettings` fields and function signatures used identically across Tasks 1→3→4→5. `classify_request` returns the same three string literals everywhere. `init_routes(oauth, settings)` signature matches its call in Task 5. ✓
