# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

<!-- 12-rule template — do not remove -->
## Development Rules

These rules apply to every task in this project unless explicitly overridden.
Bias: caution over speed on non-trivial work. Use judgment on trivial tasks.

## Rule 1 - Think Before Coding
State assumptions explicitly. If uncertain, ask rather than guess.
Present multiple interpretations when ambiguity exists.
Push back when a simpler approach exists.
Stop when confused. Name what is unclear.

## Rule 2 - Simplicity First
Minimum code that solves the problem. Nothing speculative.
No features beyond what was asked. No abstractions for single-use code.
Test: would a senior engineer say this is overcomplicated? If yes, simplify.

## Rule 3 - Surgical Changes
Touch only what you must. Clean up only your own mess.
Do not improve adjacent code, comments, or formatting.
Do not refactor what is not broken. Match existing style.

## Rule 4 - Goal-Driven Execution
Define success criteria. Loop until verified.
Do not follow steps. Define success and iterate.
Strong success criteria let you loop independently.

## Rule 5 - Use the model only for judgment calls
Use me for: classification, drafting, summarization, extraction.
Do NOT use me for: routing, retries, deterministic transforms.
If code can answer, code answers.

## Rule 6 - Token budgets are not advisory
Per-task: 4,000 tokens. Per-session: 30,000 tokens.
If approaching budget, summarize and start fresh.
Surface the breach. Do not silently overrun.

## Rule 7 - Surface conflicts, do not average them
If two patterns contradict, pick one (more recent / more tested).
Explain why. Flag the other for cleanup.
Do not blend conflicting patterns.

## Rule 8 - Read before you write
Before adding code, read exports, immediate callers, shared utilities.
Looks orthogonal is dangerous. If unsure why code is structured a way, ask.

## Rule 9 - Tests verify intent, not just behavior
Tests must encode WHY behavior matters, not just WHAT it does.
A test that cannot fail when business logic changes is wrong.

## Rule 10 - Checkpoint after every significant step
Summarize what was done, what is verified, what is left.
Do not continue from a state you cannot describe back.
If you lose track, stop and restate.

## Rule 11 - Match the codebase conventions, even if you disagree
Conformance > taste inside the codebase.
If you genuinely think a convention is harmful, surface it. Do not fork silently.

## Rule 12 - Fail loud
Completed is wrong if anything was skipped silently.
Tests pass is wrong if any were skipped.
Default to surfacing uncertainty, not hiding it.

---

## Commands

```bash
# Run locally
uvicorn main:app --reload --port 8080

# Docker
docker build -t bl-auditor:latest .
docker run -p 8080:8080 \
  -e LLM_BASE_URL=... -e LLM_API_KEY=... -e LLM_MODEL=... \
  -v bl_auditor_data:/app/data \
  bl-auditor:latest

# RabbitMQ consumer (optional, separate process)
python -m app.consumer
```

No test suite exists. No lint config exists.

---

## Architecture

**BL Auditor** is a FastAPI app that audits BuyLead product listings via a parallel LLM agent pipeline.

**Stack:** FastAPI + Jinja2 (SSR, dark-mode vanilla JS) + LangGraph 0.2 + LangChain + aio-pika (RabbitMQ consumer). Storage is append-only CSV — no database.

### Request Flow

```
POST /audit {offer_id}
  → fetch_buylead_detail()         # HTTP fetch with retries
  → build_audit_payload_from_buylead()
  → asyncio.gather() — 9 concurrent LLM agents:
      retail_agent, price_agent, buyer_viewed_agent, retail_agent_2,
      isq_validation_agent, description_agent,
      specs_vs_category_agent2, title_vs_category_agent2, title_vs_specs_agent2
  → sequential post-processing (no LLM):
      scoring_agent  → BL Score 1
      scoring_agent2 → BL Score 2
      completeness_agent → 0–15 rule-based completeness score
  → append_audit_dashboard_row()   # CSV: audit_dashboard_log.csv
  → trace logged to audit_traces.csv
  → render HTML (or SSE stream)
```

### Key Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app creation, static mount, router include |
| `app/routers/audit.py` | All HTTP endpoints (`/`, `/audit`, `/batch`, `/records`, `/traces`, `/download/*`, `/admin_view/prompt/*`) |
| `app/services/buylead_service.py` | `fetch_buylead_detail()`, `build_audit_payload_from_buylead()` |
| `app/services/audit_log_service.py` | CSV append (116 columns), `read_audit_dashboard_rows()` |
| `app/services/trace_service.py` | `AuditTrace` per-request step logging → `audit_traces.csv` |
| `app/services/prompt_override_service.py` | `get_active_prompt(agent)` — returns bundled or user-saved override from `data/prompt_overrides/` |
| `app/consumer.py` | Optional async RabbitMQ worker; reads offer IDs, POSTs to `/audit` |
| `mcat_data.xlsx`, `evidence_data.xlsx`, `evidence_data2.csv` | Reference data loaded once at startup via `@lru_cache` |

### Agents

**Concurrent (9 — run via `asyncio.gather()`):**

| Agent | What it checks | Key output fields |
|-------|---------------|-------------------|
| `retail_agent` | RETAIL / NON-RETAIL / UNCLASSIFIED buyer type | verdict, score |
| `retail_agent_2` | Cross-check retail via direct HTTP (not LangGraph) | classification, confidence |
| `price_agent` | Price plausibility vs MCAT; CORRECT / SLIGHTLY_OFF / INCORRECT / UNCERTAIN | verdict, score |
| `buyer_viewed_agent` | Relatedness of buyer's previously enquired products to the BuyLead — outlier / not_outlier (Pydantic-validated) | status, reason, product_matched |
| `isq_validation_agent` | ISQ spec coherence | verdict, score, confidence |
| `description_agent` | Description coherence | verdict, score, confidence |
| `specs_vs_category_agent2` | Specs against category rules | verdict, score, confidence |
| `title_vs_category_agent2` | Title against category rules | verdict, score, confidence |
| `title_vs_specs_agent2` | Title consistency with specs | verdict, score, confidence |

**Sequential post-processing (no LLM):**

| Agent | Output | Notes |
|-------|--------|-------|
| `scoring_agent` | BL Score 1 (0–100) | Weighted composite; weights in `app/scoring_agent/prompt.md` YAML |
| `scoring_agent2` | BL Score 2 (0–100) | Alternative weighting |
| `completeness_agent` | Completeness Score 0–15 | 5 factors × 0–3: title, quantity, spec_count, predicted_specs, description |

### Agent Pattern

Each agent lives in `app/<agent_name>/agent.py` and exposes a single async `run_<agent_name>()` function returning:
```python
{"verdict": str, "reason": str, "score": float, "duration_ms": int, ...}
```

LLM agents use LangGraph + LangChain with prompt injection. On timeout/parse error they retry up to `LLM_MAX_RETRIES` (default 2), then return an error dict. Post-processing agents (`scoring_agent`, `completeness_agent`) are purely deterministic.

Prompt overrides: users edit prompts via `/admin_view/prompt/<agent>` → saved to `data/prompt_overrides/<agent>.md`. `get_active_prompt()` checks that path first; no restart needed.

### Environment Variables

Required:
```
LLM_BASE_URL        # LLM proxy base URL
LLM_API_KEY         # Auth token
LLM_MODEL           # Model identifier
BUYLEAD_API_URL     # BuyLead HTTP API endpoint
BUYLEAD_API_KEY     # BuyLead auth token
```

Optional (with defaults):
```
LLM_TIMEOUT=60
LLM_MAX_RETRIES=2
DATA_DIR=.          # Set to /app in Docker; never use /tmp
LOG_LEVEL=INFO
```

Google login (required — app refuses to boot without the first three):
```
GOOGLE_CLIENT_ID                                     # Google OAuth client ID
GOOGLE_CLIENT_SECRET                                 # Google OAuth client secret
SESSION_SECRET                                       # random secret; signs the session cookie
ALLOWED_EMAIL_DOMAINS=indiamart.com,intermesh.net    # comma-separated allowed email domains
OAUTH_REDIRECT_BASE_URL=https://<host>               # builds the /auth/callback URL
TRUSTED_INGEST_CIDRS=127.0.0.1/32,::1/128            # peers allowed to POST /audit without login
SESSION_MAX_AGE=604800                               # session lifetime seconds (7 days)
ADMIN_EMAIL=admin@admin.com                          # admin-login identity (password login path)
ADMIN_PASSWORD=admin123                              # admin-login password; use a strong value in prod
```

Admin-login path: a password form on `/login` POSTs to `POST /auth/admin`, logging in as `ADMIN_EMAIL` with full access, bypassing Google OAuth and the domain allowlist.

All routes require Google login except `/login`, `/auth/*`, `/static/*`,
`/health`, and `POST /audit` from a trusted peer IP (the consumer). The
`/audit` IP exemption uses the real TCP peer only (never `X-Forwarded-For`).
**Proxy-safety caveat:** if a reverse proxy on the same host fronts the app,
proxied browser traffic also arrives as `127.0.0.1` — block `POST /audit` at
the proxy (or point the consumer at an internal-only port) so unauthenticated
users cannot reach it. OAuth client setup: create a Web-application OAuth 2.0
Client ID in Google Cloud with redirect URI `<OAUTH_REDIRECT_BASE_URL>/auth/callback`.

Buyer Profile agent (3 history APIs, shared AK):
```
ACCESS_TOKEN                                          # shared AK (JWT) for all 3 buyer-profile APIs
PREV_LEADS_API_BASE_URL=http://stg-leads.imutils.com  # prev BuyLeads + prev enquiries host
USER_DETAIL_API_BASE_URL=http://stg-users.imutils.com # user detail host
BUYER_PROFILE_MAX_RETRIES=2
```

RabbitMQ consumer only:
```
RABBITMQ_URL        # amqp://user:pass@host:port/vhost
RABBITMQ_QUEUE=BL_AUDITOR
RABBITMQ_PREFETCH=1
AUDIT_API_URL=http://localhost:8000/audit
AUDIT_HTTP_TIMEOUT=180
MAX_RETRIES=3
MAX_OFFERS=1000             # offers audited per calendar day; rest acked & discarded
AUDIT_SAMPLE_EVERY=10       # audit the 1st of every N valid offers (1 = audit all)
AUDIT_DAY_TZ=Asia/Kolkata   # tz for the daily budget reset (midnight)
```

The consumer runs continuously: per `AUDIT_DAY_TZ` calendar day it audits up to
`MAX_OFFERS` valid offers, sampling 1 of every `AUDIT_SAMPLE_EVERY`; all other
offers are acked (consumed) but not audited. Daily counters persist to
`DATA_DIR/consumer_state.json` (atomic write after each audit), so the cap holds
across restarts/deploys. **Single-consumer only** — multiple consumers sharing
the file would race; that case needs shared state (Redis/DB).

### Adding a New Agent

1. Create `app/<name>/agent.py` with `async def run_<name>(...) -> dict`
2. Export from `app/<name>/__init__.py`
3. Import and call in `app/routers/audit.py` — add to `asyncio.gather()` (concurrent) or after it (post-processing)
4. Add output columns to `audit_log_service.py` CSV schema

### SSE Streaming

`/batch/stream` uses `StreamingResponse` with `media_type="text/event-stream"`. Events are formatted as `data: {JSON}\n\n` and include `{index, total, offer_id, status, steps}`. Headers include `X-Accel-Buffering: no` (required for nginx proxy buffering). Error events carry a `status: "error"` field.

### Reference Data

Loaded once at startup via `@lru_cache` — requires restart to pick up changes:

| File | Used by |
|------|---------|
| `mcat_data.xlsx` | `price_agent`, `retail_agent` |
| `evidence_data.xlsx`, `evidence_data2.csv` | `price_agent` |
| `QTY_LESS_MCAT.csv` | `completeness_agent` — MCATs where quantity ISQ is not scored |

### Gotchas

- **CSV columns are order-sensitive** — never reorder or remove without updating all consumers
- **LangGraph pinned to 0.2.x** — 0.3+ has breaking API changes
- **Pydantic v2** — v1 syntax breaks silently in some places
- **Reference files load at startup** — changes to xlsx/csv require restart
- **DATA_DIR** — must be a persistent volume in Docker; `/tmp` loses data on container restart
- **Scoring weights** live in `app/scoring_agent/prompt.md` (YAML) — editing them changes scores without a code change, but does require restart due to `@lru_cache`
- **`retail_agent_2` uses direct HTTP** (not LangGraph) — its error handling differs from other LLM agents
