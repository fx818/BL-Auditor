# Admin Login — Design Spec

**Date:** 2026-07-02

## Goal

Add a second login path to `login.html`: an "Admin login" button below the
existing "Sign in with Google" button. Clicking it reveals a password form.
Submitting the correct password logs the user in with the **exact same session
shape** as a Google login (`request.session["user"] = {"email": <admin_email>}`),
granting full access to every route. Google is not involved in this path.

## Decisions (confirmed with user)

- **audit_done_by:** admin-login audits record `admin@admin.com` (the session
  email). No special-casing — `_audit_actor()` already reads `session["user"]["email"]`.
- **Credentials:** env-configurable. `ADMIN_EMAIL` (default `admin@admin.com`)
  and `ADMIN_PASSWORD` (default `admin123`). Defaults exist, so NOT fail-loud.
- **Flow:** server-side POST form (no JS-held secret; wrong password re-renders
  the login page with an error).

## Components

### 1. Config — `app/auth/config.py`

- Add two fields to the frozen `AuthSettings` dataclass:
  `admin_email: str`, `admin_password: str`.
- In `load_settings()`, read them with defaults:
  `os.environ.get("ADMIN_EMAIL", "admin@admin.com")`,
  `os.environ.get("ADMIN_PASSWORD", "admin123")`.
- New pure function:
  ```python
  def verify_admin_password(candidate: str, admin_password: str) -> bool
  ```
  Returns `True` iff `candidate` matches, using `hmac.compare_digest` for
  constant-time comparison. Returns `False` for empty/None candidate.

### 2. Route — `app/auth/routes.py`

- New `POST /auth/admin` handler:
  - Read form field `password` (via `await request.form()`).
  - If `verify_admin_password(password, _settings.admin_password)`:
    - `request.session["user"] = {"email": _settings.admin_email}`
    - `next_url = request.session.pop("next", "/") or "/"`
    - `return RedirectResponse(next_url, status_code=302)`
  - Else: re-render `login.html` with `{"error": "Incorrect password."}` and
    HTTP status 401.
- Path is under `/auth/*`, already classified `public` by `classify_request`,
  so no middleware change is needed.
- The admin path never calls `is_allowed_email`, so `admin@admin.com` not being
  an allowed domain is irrelevant.

### 3. Template — `app/templates/login.html`

- Below the Google link, add:
  - An "Admin login" button (`type="button"`) that toggles the visibility of a
    hidden password form via a tiny inline vanilla-JS handler (matching the
    page's existing inline-style, no-framework approach).
  - A `<form method="post" action="/auth/admin">` containing a
    `<input type="password" name="password">` and a submit button.
- When the template context has `error`, render a small error banner above the
  form and keep the form visible.
- The normal `GET /login` passes no `error`, so the form stays collapsed and no
  banner shows.

### 4. Config docs — `.env` / `.env.example` / `CLAUDE.md`

- Append `ADMIN_EMAIL` and `ADMIN_PASSWORD` to the Google-login block in both
  `.env` and `.env.example` (with the default values as examples; user updates
  real values).
- Add the two vars to the Google-login env block documented in `CLAUDE.md`.

## Data Flow

```
GET /login                    → login.html (form collapsed, no error)
click "Admin login"           → JS reveals password form (no network call)
POST /auth/admin {password}   → verify_admin_password()
   match   → session["user"]={"email": admin_email} → redirect to next ("/")
   no match→ re-render login.html {error} @ 401
subsequent requests           → AuthMiddleware sees session["user"] → full access
```

## Error Handling

- Wrong/empty password: re-render `login.html` with error banner, HTTP 401.
  No redirect, no session mutation.
- Already-logged-in user hitting `/login` still redirects home (unchanged).

## Testing

- `verify_admin_password`: correct password → True; wrong → False; empty/None →
  False; constant-time path exercised.
- `load_settings`: `admin_email`/`admin_password` default correctly and honor
  env overrides.
- `POST /auth/admin` (via TestClient): correct password sets session + 302 to
  `next`; wrong password → 401, no session, error in body; honors stored `next`.
- Session shape parity: after admin login, `session["user"]["email"]` equals the
  configured admin email (so `_audit_actor` records it).

## Security Note

`admin123` is a weak, shared secret and this path bypasses the domain allowlist —
anyone reaching `/login` may attempt it. Acceptable for internal/dev use as
specified; a stronger `ADMIN_PASSWORD` is a one-line env change for real
deployments.

## Out of Scope

- Rate limiting / lockout on failed admin attempts.
- Per-user admin accounts (single shared credential only).
- Any change to the Google login path or the consumer trusted-peer exemption.
