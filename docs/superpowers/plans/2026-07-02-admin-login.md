# Admin Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an env-configured admin-login path (button + password form on `login.html`) that logs a user in with the same session shape as Google, granting full access.

**Architecture:** A new `verify_admin_password` pure fn + two `AuthSettings` fields in `app/auth/config.py`; a new `POST /auth/admin` route in `app/auth/routes.py` that sets `session["user"]={"email": admin_email}` on success; a password form + toggle button in `login.html`. `/auth/*` is already public in the middleware, so no gate change.

**Tech Stack:** FastAPI + Starlette, Jinja2 SSR, pytest + Starlette TestClient, Python `hmac`.

## Global Constraints

- Session shape MUST be exactly `request.session["user"] = {"email": <admin_email>}` — identical to the Google callback — so all existing gates and `_audit_actor` work unchanged.
- Credentials are env-configurable with defaults: `ADMIN_EMAIL` default `admin@admin.com`, `ADMIN_PASSWORD` default `admin123`. Defaults exist → NOT fail-loud (do not add to `_require`).
- Password comparison MUST be constant-time (`hmac.compare_digest`).
- The admin path MUST NOT call `is_allowed_email` and MUST NOT touch the Google path or the trusted-peer `/audit` exemption.
- Wrong/empty password → re-render `login.html` with an error, HTTP 401, no session mutation, no redirect.
- Match existing code style: frozen dataclass, `os.environ.get(...)` with defaults, inline-styled standalone `login.html` (does not extend `base.html`), vanilla JS.

---

### Task 1: Config — settings fields + `verify_admin_password`

**Files:**
- Modify: `app/auth/config.py`
- Test: `tests/test_admin_config.py` (create)

**Interfaces:**
- Consumes: existing `AuthSettings` dataclass, `load_settings()`.
- Produces:
  - `AuthSettings.admin_email: str`, `AuthSettings.admin_password: str`
  - `verify_admin_password(candidate: str, admin_password: str) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_admin_config.py`:
```python
import os
from app.auth.config import verify_admin_password, load_settings


def test_verify_admin_password_correct():
    assert verify_admin_password("admin123", "admin123") is True


def test_verify_admin_password_wrong():
    assert verify_admin_password("nope", "admin123") is False


def test_verify_admin_password_empty():
    assert verify_admin_password("", "admin123") is False
    assert verify_admin_password(None, "admin123") is False


def test_load_settings_admin_defaults(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    monkeypatch.setenv("SESSION_SECRET", "z")
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    s = load_settings()
    assert s.admin_email == "admin@admin.com"
    assert s.admin_password == "admin123"


def test_load_settings_admin_env_override(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    monkeypatch.setenv("SESSION_SECRET", "z")
    monkeypatch.setenv("ADMIN_EMAIL", "boss@corp.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret")
    s = load_settings()
    assert s.admin_email == "boss@corp.com"
    assert s.admin_password == "s3cret"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_admin_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'verify_admin_password'` (and dataclass has no `admin_email`).

- [ ] **Step 3: Write minimal implementation**

In `app/auth/config.py`, add `import hmac` at the top (after the existing imports).

Add two fields to the `AuthSettings` dataclass (append after `https_only`):
```python
    admin_email: str
    admin_password: str
```

In `load_settings()`, add these to the `AuthSettings(...)` constructor call (after `https_only=...`):
```python
        admin_email=os.environ.get("ADMIN_EMAIL", "admin@admin.com"),
        admin_password=os.environ.get("ADMIN_PASSWORD", "admin123"),
```

Add the pure function (place it after `is_allowed_email`):
```python
def verify_admin_password(candidate, admin_password) -> bool:
    if not candidate or not admin_password:
        return False
    return hmac.compare_digest(str(candidate), str(admin_password))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_admin_config.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/auth/config.py tests/test_admin_config.py
git commit -m "feat: admin credentials config + verify_admin_password"
```

---

### Task 2: Route — `POST /auth/admin`

**Files:**
- Modify: `app/auth/routes.py`
- Test: `tests/test_admin_route.py` (create)

**Interfaces:**
- Consumes: `verify_admin_password` (Task 1), `_settings.admin_email`, `_settings.admin_password`, `init_routes`, module `router`, `templates`.
- Produces: `POST /auth/admin` route.

**Note on test setup:** The route reads module globals `_settings` set via `init_routes`. The test builds a minimal Starlette app: include `router`, add `SessionMiddleware`, call `init_routes(oauth=None, settings=<AuthSettings with admin_email/admin_password>)`. Construct `AuthSettings` directly (frozen dataclass) rather than env, to avoid requiring Google vars. Use `follow_redirects=False` to assert the 302.

- [ ] **Step 1: Write the failing test**

Create `tests/test_admin_route.py`:
```python
from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

from app.auth.config import AuthSettings
from app.auth import routes as auth_routes


def _make_client(admin_email="admin@admin.com", admin_password="admin123"):
    settings = AuthSettings(
        google_client_id="x",
        google_client_secret="y",
        session_secret="z",
        allowed_domains=frozenset({"indiamart.com"}),
        trusted_cidrs=(),
        redirect_base="http://localhost:8000",
        session_max_age=3600,
        https_only=False,
        admin_email=admin_email,
        admin_password=admin_password,
    )
    app = FastAPI()
    app.include_router(auth_routes.router)

    @app.get("/whoami")
    async def whoami(request: Request):  # echoes the session identity for parity assertions
        return {"email": (request.session.get("user") or {}).get("email")}

    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    auth_routes.init_routes(oauth=None, settings=settings)
    return TestClient(app)


def test_admin_login_correct_password_redirects_and_sets_session():
    client = _make_client()
    r = client.post("/auth/admin", data={"password": "admin123"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    # session cookie set; hitting /login now redirects home
    r2 = client.get("/login", follow_redirects=False)
    assert r2.status_code == 302
    assert r2.headers["location"] == "/"


def test_admin_login_wrong_password_401_no_session():
    client = _make_client()
    r = client.post("/auth/admin", data={"password": "wrong"}, follow_redirects=False)
    assert r.status_code == 401
    assert "Incorrect password" in r.text
    # not logged in: /login still shows the page (200), not a redirect
    r2 = client.get("/login", follow_redirects=False)
    assert r2.status_code == 200


def test_admin_login_session_email_matches_configured_admin_email():
    # Global Constraint: session shape is {"email": <admin_email>} so
    # _audit_actor records the configured admin email, honoring env override.
    client = _make_client(admin_email="boss@corp.com", admin_password="s3cret")
    r = client.post("/auth/admin", data={"password": "s3cret"}, follow_redirects=False)
    assert r.status_code == 302
    who = client.get("/whoami").json()
    assert who["email"] == "boss@corp.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_admin_route.py -v`
Expected: FAIL — 404 on `POST /auth/admin` (route not defined).

- [ ] **Step 3: Write minimal implementation**

In `app/auth/routes.py`:

Update the import line to add `verify_admin_password`:
```python
from app.auth.config import is_allowed_email, verify_admin_password
```

Add the route (place it after `auth_callback`, before `auth_logout`):
```python
@router.post("/auth/admin")
async def auth_admin(request: Request):
    form = await request.form()
    password = form.get("password")
    if not verify_admin_password(password, _settings.admin_password):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Incorrect password."}, status_code=401
        )
    request.session["user"] = {"email": _settings.admin_email}
    next_url = request.session.pop("next", "/") or "/"
    return RedirectResponse(next_url, status_code=302)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_admin_route.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/auth/routes.py tests/test_admin_route.py
git commit -m "feat: POST /auth/admin login route"
```

---

### Task 3: Template — admin button + password form

**Files:**
- Modify: `app/templates/login.html`

**Interfaces:**
- Consumes: `error` template var (optional; set by Task 2 on failure).
- Produces: rendered admin button + collapsible password form.

**Note:** No automated test for the template (SSR HTML); Task 2's route test already asserts the error string renders. Verify manually by reading the rendered output logic. Keep the existing inline-style approach; do not introduce a CSS/JS build step.

- [ ] **Step 1: Edit the template**

In `app/templates/login.html`, replace the single Google-link line:
```html
      <a href="/auth/login" class="btn btn-primary">Sign in with Google</a>
```
with:
```html
      <a href="/auth/login" class="btn btn-primary">Sign in with Google</a>

      <div style="margin-top:16px;">
        <button type="button" class="btn" id="admin-toggle"
                style="background:transparent;border:1px solid var(--border,#333);color:var(--text-muted,#888);">
          Admin login
        </button>
      </div>

      <form method="post" action="/auth/admin" id="admin-form"
            style="margin-top:16px;display:{% if error %}block{% else %}none{% endif %};">
        {% if error %}
        <p style="color:#d33;margin:0 0 8px;">{{ error }}</p>
        {% endif %}
        <input type="password" name="password" placeholder="Admin password"
               required autofocus
               style="display:block;width:100%;box-sizing:border-box;padding:8px;margin:0 0 8px;
                      border:1px solid var(--border,#333);border-radius:6px;" />
        <button type="submit" class="btn btn-primary" style="width:100%;">Log in as admin</button>
      </form>

      <script>
        document.getElementById('admin-toggle').addEventListener('click', function () {
          var f = document.getElementById('admin-form');
          f.style.display = f.style.display === 'none' ? 'block' : 'none';
        });
      </script>
```

- [ ] **Step 2: Verify render logic manually**

Confirm by inspection:
- With no `error` (normal `GET /login`): form `display:none`, no banner, toggle button reveals it.
- With `error` set (failed `POST /auth/admin`): form `display:block`, red banner shows the message.

- [ ] **Step 3: Commit**

```bash
git add app/templates/login.html
git commit -m "feat: admin login button + password form on login page"
```

---

### Task 4: Config docs — `.env`, `.env.example`, `CLAUDE.md`

**Files:**
- Modify: `.env`
- Modify: `.env.example`
- Modify: `CLAUDE.md`

**Interfaces:** none (documentation/config only).

- [ ] **Step 1: Edit `.env.example`**

In `.env.example`, inside the Google-login block, after the `SESSION_MAX_AGE` line, add:
```
ADMIN_EMAIL=admin@admin.com                  # admin-login email (session identity for the password login)
ADMIN_PASSWORD=admin123                      # admin-login password; USE A STRONG VALUE in real deployments
```

- [ ] **Step 2: Edit `.env`**

In `.env`, inside the Google-login block (near `SESSION_MAX_AGE`), add the same two lines:
```
ADMIN_EMAIL=admin@admin.com
ADMIN_PASSWORD=admin123
```
(User will update the real password.)

- [ ] **Step 3: Edit `CLAUDE.md`**

In `CLAUDE.md`, in the Google-login env block (the one listing `GOOGLE_CLIENT_ID`, `SESSION_SECRET`, etc.), add two lines documenting:
```
ADMIN_EMAIL=admin@admin.com                          # admin-login identity (password login path)
ADMIN_PASSWORD=admin123                              # admin-login password; use a strong value in prod
```
Also add one sentence near the auth description noting the admin-login path: a password form on `/login` posting to `POST /auth/admin` logs in as `ADMIN_EMAIL` with full access, bypassing Google and the domain allowlist.

- [ ] **Step 4: Commit**

```bash
git add .env .env.example CLAUDE.md
git commit -m "docs: document ADMIN_EMAIL/ADMIN_PASSWORD env vars"
```

---

## Post-implementation

- Run the full suite: `python -m pytest -q` — expect all prior tests + the new admin tests green.
- OKF sync: update `.knowledge/arch-auth.md` (admin-login path) and `arch-web-ui.md` (login.html admin button), restamp, add `log.md` entries. (Handled outside the task loop, before finishing the branch.)
