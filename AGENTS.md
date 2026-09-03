# AGENTS.md — bl-auditor-project

BL Auditor is a multiagent AI auditing system for BuyLead product listings. It runs 9 parallel LLM-based validation agents plus deterministic post-processing via a LangGraph pipeline, exposes results through a FastAPI/Jinja2 web dashboard, and also accepts jobs from a RabbitMQ async consumer worker.

## Stack

- **language:** Python
- **framework:** FastAPI + LangGraph + LangChain
- **messaging:** RabbitMQ
- **templates:** Jinja2
- **llm:** LLM proxy (Google Gemini backend)

## Key Directories

- `app/routers` — Central audit orchestration — all UI and API-triggered audits funnel through _audit_handler() here
- `app/windmill_auditor_api` — Windmill integration router (/trigger-one endpoint)
- `app/services` — Business logic and service layer for audit pipeline
- `retail_auditor` — Separate retail auditor sub-application; retail_auditor/app.py is its HTTP entrypoint
- `scoring` — Scoring logic — non-obvious top-level directory separate from app/services
- `API Response format` — Sample API response JSON fixtures used for development reference; contains hardcoded credentials

### Routes
- `/login → app/auth/routes.py`
- `/ → app/routers/audit.py`
- `/trigger-one → app/windmill_auditor_api/router.py`
- `/whoami → tests/test_admin_route.py`
- `/health → main.py`

### Runtime Surfaces
- `main.py` — main entrypoint
- `retail_auditor/app.py` — HTTP server entrypoint

## Commands

### Local Development
```bash
# run (source: Dockerfile:39)
sh -c exec uvicorn main:app --host 0.0.0.0 --port ${PORT}
```

## Non-Obvious Patterns

- Dead code trap: app/price_agent/ and app/retail_agent/ directories exist but are NOT wired into the audit pipeline — they are legacy. Price anomalies come from an external Audit API; retail classification uses retail_agent_2 instead.
- Two separate entry points must both run for full functionality: main.py boots the FastAPI app; app/consumer.py is a separate RabbitMQ async worker that reads the BL_AUDITOR queue and POSTs to /audit. Neither replaces the other.
- Orphaned templates: retail_view.html and retail_batch.html exist in app/templates but the /retail route has been removed — they serve no active purpose and will mislead UI flow tracing.
- Config is entirely via environment variables loaded from .env (template: .env.example) using python-dotenv — there is no config file system. Missing vars silently degrade agent behavior rather than causing startup failures. Required vars: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, RABBITMQ_URL, AUDIT_API_URL.
- LLM calls go through an internal proxy (LLM_BASE_URL), not directly to a public Gemini endpoint — network access and env vars must target the internal proxy.
- Test coverage is sparse: 9 test files vs 76 source files (~12% ratio). Do not assume untested paths are correct.
- Committed data files detected: audit_dashboard_log.csv (repo root) and retail_auditor/data/unit_master.csv contain tabular data that should not be in version control.
- Docker build is defined under the repo root; start from Dockerfile.

## Sensitive Areas

- **`app/auth`** — Authentication layer — changes affect all users. Review all session/token handling changes carefully; any regression here locks out or exposes all users.
- **`windmill/description_auditor/flow.yaml`** — Hardcoded credentials detected in Windmill flow YAML. Do not copy or extend these files without scrubbing credentials first; rotate any exposed keys before merging.
- **`windmill/description_auditor/flow_manual_input.yaml`** — Hardcoded credentials detected. Same as flow.yaml — treat as sensitive; do not log or echo file contents in CI.
- **`.mcp.json`** — Hardcoded credential detected. Do not commit changes that expand or replicate this pattern; move secrets to env vars.
- **`API Response format`** — Sample JSON fixtures contain hardcoded credentials (prev_buylead_detail.json, userdetail_api_resp.json). Treat as read-only reference; never use these values in production code or tests.
- **`tests/test_admin_route.py`** — Hardcoded passwords present in test file. Ensure test credentials are not real production passwords; replace with clearly fake fixtures.

_Deterministic secret scan — every credential/secret found in the repo (locations only; rotate/remove, never commit):_
- **`audit_dashboard_log.csv:1`** — hardcoded secret detected by deterministic scan — do not commit/expose.
- **`retail_auditor/data/unit_master.csv:1`** — hardcoded secret detected by deterministic scan — do not commit/expose.
- **`tests/test_admin_route.py:9`** — hardcoded password detected by deterministic scan — do not commit/expose.
- **`tests/test_admin_route.py:58`** — hardcoded password detected by deterministic scan — do not commit/expose.
- **`docs/superpowers/plans/2026-07-02-admin-login.md:144`** — hardcoded password detected by deterministic scan — do not commit/expose.
- **`docs/superpowers/plans/2026-07-02-admin-login.md:193`** — hardcoded password detected by deterministic scan — do not commit/expose.

## Conventions

- All audit orchestration (UI and API) routes through _audit_handler() in app/routers/audit.py — new audit features must hook into this function, not bypass it.
- Editing retail_auditor/? Read retail_auditor/app.py first as the canonical pattern for that sub-application.
- Ignore __pycache__/ directories when tracing code or reviewing diffs.

## Notes for Agents

- Required env vars before running: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, RABBITMQ_URL, RABBITMQ_QUEUE, AUDIT_API_URL — copy .env.example and fill all values.
- The PORT env var is required at container startup (Dockerfile CMD uses ${PORT}); the app binds to 0.0.0.0:${PORT}.
- Editing retail_auditor/? Read retail_auditor/app.py first — it is the HTTP entrypoint and canonical example for that sub-application.
- data/ directory contains .xlsx and .csv reference files that agents depend on at runtime — do not delete or restructure without checking agent import paths.
- app/consumer.py is a standalone RabbitMQ worker process, not imported by main.py — it must be started separately for queue-driven audit jobs to work.
