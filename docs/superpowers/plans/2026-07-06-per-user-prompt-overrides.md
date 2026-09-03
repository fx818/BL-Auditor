# Per-User Prompt Overrides Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate each logged-in user's prompt overrides under `data/prompt_overrides/{user_email}/` so users can only see and edit their own overrides.

**Architecture:** Add a `contextvars.ContextVar` for the current user email, set it at the start of each request handler, and read it inside `get_active_prompt` — so the 14 existing agent call-sites need no changes. The fallback chain becomes: user-scoped override → global override → bundled default. Save/reset routes write to the user-scoped directory using the session email.

**Tech Stack:** Python `contextvars`, existing FastAPI session middleware, existing `prompt_override_service.py`.

## Global Constraints

- Python 3.10+; use `str | None` union syntax (no `Optional`).
- No new dependencies.
- Do NOT modify any agent files (`buyer_viewed_agent/agent.py`, etc.) — only the service and router.
- Match existing code style: no docstrings on helpers, no inline comments unless non-obvious.
- Backward-compatible: existing `data/prompt_overrides/{agent}.md` global files continue to work as fallback.

---

## File Map

| File | Change |
|------|--------|
| `app/services/prompt_override_service.py` | Add ContextVar; add `user_override_path`; change `get_active_prompt` to 3-level fallback; add `set_audit_user`; add `user_email` param to `save_override` / `reset_override` |
| `app/routers/audit.py` | Set ContextVar in `_audit_handler`; pass user email to `_build_agents_view`, `_prompts_save`, `_prompts_reset` |

---

## Task 1: Per-user storage in `prompt_override_service.py`

**Files:**
- Modify: `app/services/prompt_override_service.py`

**Interfaces:**
- Produces: `set_audit_user(email: str | None) -> None` — sets ContextVar for current request
- Produces: `user_override_path(agent_key: str, user_email: str) -> Path`
- Modified: `get_active_prompt(agent_key: str, user_email: str | None = None) -> Tuple[str, bool]` — 3-level fallback
- Modified: `save_override(agent_key: str, content: str, user_email: str | None = None) -> None`
- Modified: `reset_override(agent_key: str, user_email: str | None = None) -> None`

- [ ] **Step 1: Add ContextVar and helpers after the `OVERRIDE_DIR` definition**

Open `app/services/prompt_override_service.py`. After the `OVERRIDE_DIR = ...` line (line 17), add:

```python
from contextvars import ContextVar

_current_user: ContextVar[str | None] = ContextVar("_current_user", default=None)


def set_audit_user(email: str | None) -> None:
    _current_user.set((email or "").strip().lower() or None)
```

- [ ] **Step 2: Add `user_override_path` function after the existing `override_path` function**

After the `override_path` function (currently at line 80–82), add:

```python
def user_override_path(agent_key: str, user_email: str) -> Path:
    _require(agent_key)
    return OVERRIDE_DIR / user_email.strip().lower() / f"{agent_key}.md"
```

- [ ] **Step 3: Replace `get_active_prompt` with 3-level fallback**

Replace the existing `get_active_prompt` function body:

```python
def get_active_prompt(agent_key: str, user_email: str | None = None) -> Tuple[str, bool]:
    """Return ``(prompt_text, is_override)`` for the agent.

    Fallback order: user-scoped override → global override → bundled default.
    ``user_email`` overrides the ContextVar when provided explicitly.
    """
    email = user_email or _current_user.get()
    if email:
        user_ov = user_override_path(agent_key, email)
        if user_ov.exists():
            text = user_ov.read_text(encoding="utf-8")
            if text.strip():
                return text, True
    ov = override_path(agent_key)
    if ov.exists():
        text = ov.read_text(encoding="utf-8")
        if text.strip():
            return text, True
    return bundled_path(agent_key).read_text(encoding="utf-8"), False
```

- [ ] **Step 4: Update `save_override` to accept `user_email`**

Replace the existing `save_override` function:

```python
def save_override(agent_key: str, content: str, user_email: str | None = None) -> None:
    """Atomically write the override file (user-scoped if email given, else global)."""
    _require(agent_key)
    email = user_email or _current_user.get()
    target = user_override_path(agent_key, email) if email else override_path(agent_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{agent_key}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
```

- [ ] **Step 5: Update `reset_override` to accept `user_email`**

Replace the existing `reset_override` function:

```python
def reset_override(agent_key: str, user_email: str | None = None) -> None:
    email = user_email or _current_user.get()
    ov = user_override_path(agent_key, email) if email else override_path(agent_key)
    if ov.exists():
        ov.unlink()
```

- [ ] **Step 6: Update the `save_override` import in the module exports (add `set_audit_user`)**

In `app/routers/audit.py`, add `set_audit_user` and `user_override_path` to the import from `prompt_override_service`:

```python
from app.services.prompt_override_service import (
    ADMIN_ONLY_AGENTS as PROMPT_ADMIN_ONLY,
    AGENTS as PROMPT_AGENTS,
    PUBLIC_AGENTS as PROMPT_PUBLIC,
    get_active_prompt,
    get_bundled_prompt,
    reset_override,
    save_override,
    set_audit_user,
)
```

- [ ] **Step 7: Manual smoke test — verify the service logic works**

Start a Python REPL in the project root:

```python
import os; os.environ.setdefault("DATA_DIR", ".")
from app.services.prompt_override_service import *

# Should return bundled (no override exists yet)
text, is_ov = get_active_prompt("description")
assert not is_ov, "Expected bundled prompt, got override"

# Save a user-scoped override
save_override("description", "## my test override\n", user_email="test@indiamart.com")
text, is_ov = get_active_prompt("description", user_email="test@indiamart.com")
assert is_ov and "my test override" in text

# Different user should NOT see this override
text2, is_ov2 = get_active_prompt("description", user_email="other@indiamart.com")
assert not is_ov2

# ContextVar path
set_audit_user("test@indiamart.com")
text3, is_ov3 = get_active_prompt("description")
assert is_ov3 and "my test override" in text3

# Reset
reset_override("description", user_email="test@indiamart.com")
text4, is_ov4 = get_active_prompt("description", user_email="test@indiamart.com")
assert not is_ov4

print("All assertions passed")
```

Expected output: `All assertions passed`

- [ ] **Step 8: Clean up test file if created**

```bash
# Remove the test override directory if it was created under the project root
rm -rf prompt_overrides/test@indiamart.com
```

- [ ] **Step 9: Commit**

```bash
git add app/services/prompt_override_service.py
git commit -m "feat: per-user prompt override storage with 3-level fallback"
```

---

## Task 2: Wire user email into the router

**Files:**
- Modify: `app/routers/audit.py` (lines ~226–248 for `_audit_handler`, ~1635–1720 for prompt routes)

**Interfaces:**
- Consumes: `set_audit_user(email)` from Task 1
- Modified: `_build_agents_view(keys, user_email=None)` — passes email to `get_active_prompt`
- Modified: `_prompts_save(request, allow_admin)` — extracts session email, passes to `save_override`
- Modified: `_prompts_reset(request, allow_admin)` — extracts session email, passes to `reset_override`

- [ ] **Step 1: Set ContextVar at the top of `_audit_handler`**

In `_audit_handler` (starts around line 233), after `actor = _audit_actor(request)`, add:

```python
set_audit_user(actor if "@" in (actor or "") else None)
```

Full context for the edit — lines 246–248 currently read:
```python
    trace = AuditTrace(offer_id)
    actor = _audit_actor(request)
    buylead_response = {}
```

Change to:
```python
    trace = AuditTrace(offer_id)
    actor = _audit_actor(request)
    set_audit_user(actor if "@" in (actor or "") else None)
    buylead_response = {}
```

This means every agent that calls `get_active_prompt(key)` will automatically pick up the requesting user's scoped override. No agent files need changes.

- [ ] **Step 2: Update `_build_agents_view` to accept and pass `user_email`**

Replace:
```python
def _build_agents_view(keys):
    out = []
    for key in keys:
        meta = PROMPT_AGENTS[key]
        content, is_override = get_active_prompt(key)
        out.append({
            "key": key,
            "display_name": meta["display_name"],
            "placeholders": meta["placeholders"],
            "content": content,
            "is_override": is_override,
        })
    return out
```

With:
```python
def _build_agents_view(keys, user_email: str | None = None):
    out = []
    for key in keys:
        meta = PROMPT_AGENTS[key]
        content, is_override = get_active_prompt(key, user_email=user_email)
        out.append({
            "key": key,
            "display_name": meta["display_name"],
            "placeholders": meta["placeholders"],
            "content": content,
            "is_override": is_override,
        })
    return out
```

- [ ] **Step 3: Extract user email in `prompts_page` and `admin_prompts_page`**

In `prompts_page`:
```python
@router.get("/prompts", response_class=HTMLResponse)
async def prompts_page(request: Request):
    user_email = (request.session.get("user") or {}).get("email")
    return templates.TemplateResponse(request, "prompts.html", {
        "agents": _build_agents_view(PROMPT_PUBLIC, user_email=user_email),
        "is_admin": False,
        "save_url": "/prompts/save",
        "reset_url": "/prompts/reset",
    })
```

In `admin_prompts_page`:
```python
@router.get("/admin_view/prompts", response_class=HTMLResponse)
async def admin_prompts_page(request: Request):
    user_email = (request.session.get("user") or {}).get("email")
    return templates.TemplateResponse(request, "prompts.html", {
        "agents": _build_agents_view(list(PROMPT_AGENTS.keys()), user_email=user_email),
        "is_admin": True,
        "save_url": "/admin_view/prompts/save",
        "reset_url": "/admin_view/prompts/reset",
    })
```

- [ ] **Step 4: Pass user email in `_prompts_save` and `_prompts_reset`**

Replace the `save_override(agent_key, content)` call in `_prompts_save`:

```python
async def _prompts_save(request: Request, allow_admin: bool):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    agent_key = str(body.get("agent", "")).strip()
    content = body.get("content", "")
    if agent_key not in PROMPT_AGENTS:
        raise HTTPException(status_code=400, detail="Unknown agent")
    if not allow_admin and agent_key in PROMPT_ADMIN_ONLY:
        raise HTTPException(status_code=403, detail="This prompt is editable only from the admin view")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content must be a string")
    user_email = (request.session.get("user") or {}).get("email")
    save_override(agent_key, content, user_email=user_email)
    _, is_override = get_active_prompt(agent_key, user_email=user_email)
    return {"ok": True, "is_override": is_override}
```

Replace `_prompts_reset`:

```python
async def _prompts_reset(request: Request, allow_admin: bool):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    agent_key = str(body.get("agent", "")).strip()
    if agent_key not in PROMPT_AGENTS:
        raise HTTPException(status_code=400, detail="Unknown agent")
    if not allow_admin and agent_key in PROMPT_ADMIN_ONLY:
        raise HTTPException(status_code=403, detail="This prompt is editable only from the admin view")
    user_email = (request.session.get("user") or {}).get("email")
    reset_override(agent_key, user_email=user_email)
    content = get_bundled_prompt(agent_key)
    return {"ok": True, "content": content, "is_override": False}
```

- [ ] **Step 5: Verify the app boots**

```bash
uvicorn main:app --port 8080
```

Expected: server starts without import errors.

- [ ] **Step 6: Manual E2E test**

1. Log in as User A → go to `/prompts` → edit "Description Agent" → save → reload page → confirm the edited text is shown, badge says "Override active".
2. Log in as User B (different account) → go to `/prompts` → confirm "Description Agent" shows the bundled default, not User A's edit.
3. As User A → reset "Description Agent" → reload → confirm bundled prompt is back.
4. Confirm the audit flow works: as User A, run an audit → the Description Agent uses User A's override (verify via trace page, `llm_messages.system` field shows the override text).

- [ ] **Step 7: Commit**

```bash
git add app/routers/audit.py
git commit -m "feat: wire per-user email into prompt view and save/reset routes"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** All requirements covered — per-user dir, isolation, 3-level fallback, UI reads/writes per-user.
- [x] **No placeholders:** All code blocks are complete and runnable.
- [x] **Type consistency:** `user_email: str | None` used consistently; `user_override_path` and `set_audit_user` defined in Task 1 and consumed in Task 2.
- [x] **Backward compat:** Global `data/prompt_overrides/{agent}.md` files remain as fallback. Consumer (no session) uses global → bundled chain unchanged.
- [x] **Agent files untouched:** All 14 `get_active_prompt(key)` call-sites in agents are unchanged; they inherit the user via ContextVar set before `asyncio.gather`.
- [x] **Admin login path:** `_audit_actor` returns `"admin"` for the consumer (no `@`), so `set_audit_user` gets `None` and falls through to global overrides — correct.
