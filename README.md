# BL Auditor

A multiagent AI auditing system for BuyLead product listings. Runs 9 concurrent agents plus 4 deterministic post-processing agents to validate product data quality across category-outlier checks, buyer intent, ISQ coherence, description alignment, specs-vs-category fit, title-vs-category fit, title-vs-specs consistency, buyer profile genuineness, and quantity plausibility. Produces two composite BL Scores and a rule-based Completeness Score, and reconstructs the BL buyer's recent activity timeline.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Agents](#agents)
- [Project Structure](#project-structure)
- [Full Request Flow](#full-request-flow)
- [Setup — Python (Local)](#setup--python-local)
- [Setup — Docker](#setup--docker)
- [RabbitMQ Consumer](#rabbitmq-consumer)
- [Environment Variables](#environment-variables)
- [Web UI Guide](#web-ui-guide)
- [API Reference](#api-reference)
- [Data Storage](#data-storage)
- [External Services](#external-services)

---

## Overview

BL Auditor accepts a BuyLead `offer_id`, fetches the full product listing, and runs it through a parallel pipeline of 9 validation agents. Results feed into 4 deterministic post-processing agents (two scoring agents, one completeness agent, one quantity-audit agent). A non-fatal buyer-activity step then reconstructs the buyer's recent timeline. Everything is displayed in a web dashboard, logged to CSV, and stored as replayable traces.

**Tech Stack:**
- **Backend:** FastAPI 0.111+, uvicorn, Pydantic v2
- **LLM Orchestration:** LangGraph 0.2, LangChain, Google Gemini via internal proxy
- **Task Queue:** RabbitMQ (aio-pika) for async consumer
- **Frontend:** Jinja2 templates, vanilla JS/CSS (dark mode)
- **Data:** CSV logging, Excel reference files

> **Note on dropped agents:** The `app/price_agent/` and `app/retail_agent/` (LangGraph) folders still exist but are **no longer wired into the audit pipeline** — they are legacy/dead code. Price anomalies are now flagged by the external Audit API; retail classification is handled by `retail_agent_2`. Likewise the standalone `/retail` view has been removed (its templates `retail_view.html` / `retail_batch.html` are orphaned).

---

## Architecture

The system is organized into five layers. Each layer has a clear responsibility and communicates only with the layer adjacent to it.

```
╔══════════════════════════════════════════════════════════════════════╗
║  LAYER 1 — ENTRY POINTS                                             ║
║                                                                      ║
║   main.py                  app/consumer.py                          ║
║   FastAPI app boot         RabbitMQ async worker                    ║
║   Mounts /static           Reads BL_AUDITOR queue                   ║
║   Registers audit router   POSTs to /audit endpoint                 ║
╚══════════════════════╤═══════════════════════════════════════════════╝
                       │
╔══════════════════════▼═══════════════════════════════════════════════╗
║  LAYER 2 — ROUTING  (app/routers/audit.py)                          ║
║                                                                      ║
║   All UI pages, API endpoints, SSE stream, download endpoints,      ║
║   prompt editor, admin views, buyer-activity viewer, demo page.     ║
║   Every audit route calls _audit_handler().                         ║
╚══════════════════════╤═══════════════════════════════════════════════╝
                       │
╔══════════════════════▼═══════════════════════════════════════════════╗
║  LAYER 3 — ORCHESTRATION  (_audit_handler in audit.py)              ║
║                                                                      ║
║  Step 1  buylead_service.fetch_buylead_detail()                     ║
║          HTTP GET → BuyLead API, retries on 5xx/timeout             ║
║                                                                      ║
║  Step 2  buylead_service.build_audit_payload_from_buylead()         ║
║          Normalises raw BuyLead fields into AuditPayload schema     ║
║                                                                      ║
║  Step 3  asyncio.gather() — 9 agents run concurrently               ║
║          (see Layer 4)                                               ║
║                                                                      ║
║  Step 4  run_scoring_agent()    → BL Score 1 (bl_score / bl_verdict)║
║          run_scoring_agent2()   → BL Score 2 (bl_score_2)           ║
║          run_completeness_agent() → Completeness Score (0–18)       ║
║          run_quantity_agent()   → Quantity Audit (Absurd/OK/...)    ║
║          (deterministic, sequential, no LLM)                        ║
║                                                                      ║
║  Step 5  _resolve_buyer_activity()  (non-fatal)                     ║
║          decrypt glid → buyer activity timeline → optional          ║
║          keyword selection + searched-terms match                   ║
║                                                                      ║
║  Step 6  audit_log_service.append_audit_dashboard_row()             ║
║          trace_service.AuditTrace.save()                            ║
║                                                                      ║
║  Step 7  Jinja2 render → result.html (4 template partials)          ║
╚══════════════════════╤═══════════════════════════════════════════════╝
                       │
╔══════════════════════▼═══════════════════════════════════════════════╗
║  LAYER 4 — AGENTS  (run concurrently via asyncio.gather)            ║
║                                                                      ║
║  ┌──────────────────────┐  ┌───────────────────────────────────┐    ║
║  │  External HTTP       │  │  LangGraph Agents (async)         │    ║
║  │                      │  │                                   │    ║
║  │  auditor_service.py  │  │  buyer_viewed_agent/agent.py      │    ║
║  │  → POST to           │  │  specs_vs_category_agent2/        │    ║
║  │    categorization_   │  │  title_vs_category_agent2/        │    ║
║  │    outlier API       │  │  title_vs_specs_agent2/           │    ║
║  └──────────────────────┘  └───────────────────────────────────┘    ║
║                                                                      ║
║  ┌───────────────────────────────────────────────────────────────┐  ║
║  │  Direct LLM Agents (sync, wrapped in asyncio.to_thread)       │  ║
║  │  retail_agent_2/   isq_validation_agent/   description_agent/ │  ║
║  └───────────────────────────────────────────────────────────────┘  ║
║                                                                      ║
║  ┌───────────────────────────────────────────────────────────────┐  ║
║  │  buyer_profile_agent/agent.py  (plain async)                  │  ║
║  │  3 history APIs + deterministic profile/tenure + LLM verdict  │  ║
║  └───────────────────────────────────────────────────────────────┘  ║
║                                                                      ║
║  All LLM agents load their system prompt via:                       ║
║  prompt_override_service.get_active_prompt(agent_key)              ║
║  → checks data/prompt_overrides/{key}.md  (user override)          ║
║  → falls back to app/{agent}/prompt.md    (bundled default)        ║
╚══════════════════════╤═══════════════════════════════════════════════╝
                       │
╔══════════════════════▼═══════════════════════════════════════════════╗
║  LAYER 4b — POST-PROCESSING  (deterministic, after asyncio.gather)  ║
║                                                                      ║
║  scoring_agent/agent.py   → BL Score 1 (weighted composite, 0–100) ║
║  scoring_agent/agent.py   → BL Score 2 (Agent-2 weighted composite) ║
║  completeness_agent/      → Completeness Score (rule-based, 0–18)  ║
║  quantity_agent/agent.py  → Quantity Audit (Absurd/OK/Unverifiable) ║
╚══════════════════════╤═══════════════════════════════════════════════╝
                       │
╔══════════════════════▼═══════════════════════════════════════════════╗
║  LAYER 5 — PERSISTENCE                                              ║
║                                                                      ║
║   data/audit_dashboard_log.csv    118-column audit results          ║
║   data/audit_traces.csv           step-by-step execution trace      ║
║   data/prompt_overrides/*.md      user-edited agent prompts         ║
║   data/consumer_state.json        consumer daily budget counters    ║
║   mcat_data.xlsx                  MCAT reference  (read-only)       ║
║   evidence_data.xlsx              price quartile ref (read-only)    ║
║   evidence_data2.csv              retail evidence   (read-only)     ║
║   QTY_LESS_MCAT.csv               quantity-exempt MCATs (read-only) ║
╚══════════════════════════════════════════════════════════════════════╝
```

### Layer-by-layer explanation

**Layer 1 — Entry Points**

`main.py` is the only boot file. It creates the FastAPI app, mounts the `static/` directory under `/static`, and includes the single router from `app/routers/audit.py`. Uvicorn runs it on port 8080.

`app/consumer.py` is a completely independent async process. It connects to RabbitMQ via `aio-pika`, reads messages off the `BL_AUDITOR` queue, and forwards each `ofr_id` as a standard HTTP POST to the FastAPI server. It never calls agent code directly — it just feeds the web server, and applies a daily audit budget + sampling (see [RabbitMQ Consumer](#rabbitmq-consumer)). Run it separately with `python -m app.consumer`.

**Layer 2 — Routing (`app/routers/audit.py`)**

All routes live here. Each route either renders a Jinja2 template or calls `_audit_handler()`. There are paired public/admin routes for the same pages (e.g. `GET /` form + `POST /audit`, and `GET /admin_view` + `POST /admin_view/audit`) — admin routes additionally expose more prompts. SSE streaming for batch audits is defined as `GET /batch/stream`. A buyer-activity timeline viewer lives at `GET /activity` (form) + `POST /activity` (fetch).

**Layer 3 — Orchestration (`_audit_handler` in `audit.py`)**

This private async function is the core of the system. Every audit — whether triggered from the UI, the JSON API, the batch stream, or the RabbitMQ consumer — runs through here. It sequences the steps described in the flow section below. The same gather + scoring + trace + CSV wiring is duplicated in the batch SSE loop and partially reconstructed in the trace-detail re-render.

**Layer 4 — Agents**

Agents split into three execution patterns:

- **LangGraph agents** (`buyer_viewed_agent`, `specs_vs_category_agent2`, `title_vs_category_agent2`, `title_vs_specs_agent2`) build a stateful async graph with nodes for input preparation, LLM call, and result parsing. These are naturally async.
- **Direct LLM agents** (`retail_agent_2`, `isq_validation_agent`, `description_agent`) format a user message, call the LLM via a thin synchronous wrapper, and parse the JSON response. Because they are sync, they run inside `asyncio.to_thread()` so they don't block the event loop.
- **External HTTP** (`auditor_service`) POSTs the compiled payload to the internal categorization API; uses `run_in_executor` since `requests` is synchronous.
- **Plain async** (`buyer_profile_agent`) fetches three buyer-history APIs, runs deterministic profile/tenure checks, then makes one LLM genuineness call.

All 9 tasks run simultaneously via `asyncio.gather()`. Every agent exposes a single public function `run_{agent_name}(offer_id, buylead_response)` and returns a plain dict.

**Layer 4b — Post-Processing (deterministic)**

After `asyncio.gather()` resolves, four deterministic agents run sequentially:

- **Scoring Agent** (`run_scoring_agent`): Computes **BL Score 1** — a weighted composite (0–100) from the Audit API, ISQ validation, description, buyer viewed, and retail2. Weights are configurable; N/A agents redistribute their weight proportionally.
- **Scoring Agent 2** (`run_scoring_agent2`): Computes **BL Score 2** using the three Agent-2 results (`specs_vs_category_agent2`, `title_vs_category_agent2`, `title_vs_specs_agent2`) along with ISQ, description, buyer, and retail2. Same weighted redistribution logic.
- **Completeness Agent** (`run_completeness_agent`): Rule-based scoring, no LLM. Scores six factors (max 3 each, total max 18): title fill, quantity ISQ presence, ISQ spec count, predicted-specs fill rate, buyer photos, and description presence.
- **Quantity Agent** (`run_quantity_agent`): Rule-based, no LLM. Flags an absurd quantity by five rules (see [Agents](#agents)) and returns Absurd / OK / Unverifiable + the fired rules.

**Layer 5 — Persistence**

No database. Two CSV files are appended after every audit. Reference Excel/CSV files are loaded once at startup with `lru_cache` and never written to. Prompt overrides are plain `.md` files written atomically by `prompt_override_service.py`. The consumer persists its daily budget counters to `consumer_state.json`.

---

## Agents

### Concurrent Agents (9, run via `asyncio.gather`)

| # | Agent | Method | Purpose | Output Fields |
|---|-------|--------|---------|---------------|
| 1 | **Audit API** | External HTTP POST | Specs/title category outlier + title-spec verdict | `specs_category_outlier_status`, `title_category_outlier_status`, `title_spec_verdict` |
| 2 | **Buyer Viewed Agent** | LangGraph | Relatedness of buyer's previously enquired/viewed products to the BuyLead | `Genuineness`, `Profile_Score`, `Confidence`, `Profile_Reason`, `product_match` |
| 3 | **Retail Agent 2** | Direct LLM | Classify buyer intent (Retail vs Non-Retail) | `Classification`, `Confidence`, `Reason` |
| 4 | **ISQ Validation Agent** | Direct LLM | Check ISQ coherence (duplicates, validity) | `Status`, `Score`, `Confidence`, `Reason`, `Issues` |
| 5 | **Description Agent** | Direct LLM | Validate description vs title + MCAT | `Status`, `Score`, `Confidence`, `Reason`, `Issues` |
| 6 | **Specs vs Category Agent 2** | LangGraph | Verify ISQ specs are valid for the MCAT | `Status`, `Score`, `Confidence`, `Reason`, `Issues` |
| 7 | **Title vs Category Agent 2** | LangGraph | Verify product title matches the MCAT | `Status`, `Score`, `Confidence`, `Reason`, `Issues` |
| 8 | **Title vs Specs Agent 2** | LangGraph | Check title-to-ISQ spec consistency | `Status`, `Score`, `Confidence`, `Reason`, `Issues` |
| 9 | **Buyer Profile Agent** | Plain async (3 APIs + LLM) | Buyer genuineness from prev BuyLeads/enquiries + profile completeness + tenure | `Genuineness`, `Score`, `Confidence`, `Reason`, `Status`, `check_reason`, `tenure` |

> The **Buyer Profile Agent is standalone** — it is *not* part of BL Score 1 or BL Score 2. It is distinct from the Buyer Viewed Agent despite the similar name. (The `buyer_profile` *scoring weight key* in the scoring YAML is bound to the Buyer Viewed Agent, a separate thing again.)

### Post-Processing Agents (deterministic, sequential after gather)

| # | Agent | Method | Purpose | Output Fields |
|---|-------|--------|---------|---------------|
| 10 | **Scoring Agent** | Deterministic (no LLM) | BL Score 1 — weighted composite from Audit API + ISQ + desc + buyer viewed + retail2 | `composite_score`, `verdict` (Approved / Needs Review / Do Not Approve), `agent_breakdown` |
| 11 | **Scoring Agent 2** | Deterministic (no LLM) | BL Score 2 — weighted composite from Agent-2 results + ISQ + desc + buyer + retail2 | `composite_score`, `verdict`, `agent_breakdown` |
| 12 | **Completeness Agent** | Deterministic rule-based | Listing completeness across 6 factors (0–18 max) | `total_score`, `percentage`, `breakdown` |
| 13 | **Quantity Agent** | Deterministic rule-based | Flag absurd quantities (5 rules) | `status` (Absurd / OK / Unverifiable), `reason`, `rules_fired` |

**Quantity Agent rules** (reuses `price_agent` parse helpers): `price_equals_qty` (qty>1000 equals an enquired product price), `qty_in_price_band` (qty>1000 inside the MCAT [q1,q3] band), `too_many_digits` (≥6 digits), `sequence_or_repeat` (ascending/descending run or a leading digit repeated ≥3×), and `heavy_unit` (tonne/quintal/truckload/container/wagon families, each with a per-family quantity threshold).

All LLM agents are prompt-overridable via the `/prompts` web UI.

### Buyer Activity (post-gather, non-fatal)

After scoring, `_resolve_buyer_activity()` reconstructs the buyer's recent timeline (browse / search / enquiry / BuyLead events):

1. Decrypt the encrypted buyer glid from `FK_GLUSR_USR_ID` (`glid_crypto_service.decrypt_glid`).
2. Fetch the buyer-activity timeline (`buyer_activity_service.fetch_buyer_activity`) for a window from `(offer − 2 days) 00:00` to the offer moment.
3. **Only when** the description starts with `"Buyer Searched for"` **and** the description verdict is `Incorrect`, two extra steps run:
   - `select_activity_keywords()` — LLM picks the top ≤4 activity keywords by MCAT relevance (logged to `activity_keywords`).
   - `match_searched_terms()` — deterministic (no LLM): splits the description on commas and reports each term as found/not-found against the activity-log keywords (result-page only; not logged to CSV).

Any failure in this block is recorded as a trace step and does not fail the audit.

---

## Project Structure

```
bl-auditor-project/
├── main.py                          # FastAPI entry point, mounts static + router
├── requirements.txt
├── Dockerfile                       # Multistage build (Python 3.12-slim)
├── .env                             # Runtime secrets (not committed)
├── .env.example                     # Template for .env
├── .dockerignore
│
├── app/
│   ├── models/
│   │   └── schemas.py               # Pydantic: AuditPayload, McatPoolItem, AuditResponse
│   │
│   ├── routers/
│   │   └── audit.py                 # All HTTP routes
│   │
│   ├── services/
│   │   ├── buylead_service.py       # Fetch BuyLead API, parse ISQ, build payload
│   │   ├── auditor_service.py       # Call external categorization_outlier API
│   │   ├── buyer_activity_service.py# Fetch + parse buyer activity timeline
│   │   ├── buyer_profile_service.py # 3 buyer-history APIs + deterministic profile checks
│   │   ├── glid_crypto_service.py   # Decrypt encrypted buyer glid (RC4 keystream)
│   │   ├── audit_log_service.py     # Append/read audit_dashboard_log.csv (118 fields)
│   │   ├── trace_service.py         # Save/list/get audit traces
│   │   └── prompt_override_service.py # Load/save/reset agent prompts
│   │
│   ├── consumer.py                  # Async RabbitMQ worker (daily budget + sampling)
│   │
│   ├── retail_agent_2/              # Direct-LLM retail classification
│   │   ├── agent.py
│   │   ├── auditor_llm.py
│   │   ├── prompt.md
│   │   ├── few_shots.md
│   │   └── result_format.md
│   │
│   ├── buyer_viewed_agent/          # LangGraph buyer-viewed-products relatedness
│   ├── isq_validation_agent/        # Direct-LLM ISQ coherence
│   ├── description_agent/           # Direct-LLM description validation + activity keywords
│   ├── specs_vs_category_agent2/    # LangGraph: ISQ specs validity for MCAT
│   ├── title_vs_category_agent2/    # LangGraph: title-to-MCAT alignment
│   ├── title_vs_specs_agent2/       # LangGraph: title-to-ISQ consistency
│   ├── buyer_profile_agent/         # Standalone buyer genuineness/profile/tenure
│   │
│   ├── scoring_agent/               # Deterministic BL Score 1 + BL Score 2
│   ├── completeness_agent/          # Rule-based completeness scoring (0–18)
│   ├── quantity_agent/              # Rule-based quantity-audit scoring
│   │
│   ├── price_agent/                 # LEGACY — not wired into pipeline
│   ├── retail_agent/                # LEGACY — not wired into pipeline
│   │
│   └── templates/                   # Jinja2 HTML templates
│       ├── base.html
│       ├── index.html               # Single audit form
│       ├── result.html              # Audit result (calls result_part1-4)
│       ├── result_part1.html        # BuyLead + Audit API results
│       ├── result_part2.html        # Buyer Viewed + Buyer Profile
│       ├── result_part3.html        # Retail 2 + ISQ + Description
│       ├── result_part4.html        # Full trace + raw JSON
│       ├── batch.html               # Batch CSV upload with SSE progress
│       ├── activity.html            # Buyer-activity timeline viewer
│       ├── records.html             # Filterable audit log viewer
│       ├── traces.html              # List of saved traces
│       ├── trace_detail.html        # Step-by-step trace replay
│       ├── prompts.html             # Prompt editor
│       ├── audit_error.html
│       ├── retail_view.html         # LEGACY — orphaned (no route)
│       └── retail_batch.html        # LEGACY — orphaned (no route)
│
├── static/
│   ├── css/
│   │   ├── style.css                # Dark mode UI, card grid
│   │   └── excel-filter.css         # Table filter plugin
│   └── js/
│       ├── app.js                   # Form submit, verdict coloring, copy JSON, CSV export
│       └── excel-filter.js          # Column search + toggle
│
├── data/
│   ├── prompt_overrides/            # User-saved prompt overrides (auto-created)
│   ├── audit_dashboard_log.csv      # Main 118-column audit results log
│   ├── audit_traces.csv             # Trace metadata + JSON steps
│   └── consumer_state.json          # Consumer daily budget counters
│
├── mcat_data.xlsx                   # MCAT product category reference
├── evidence_data.xlsx               # Price quartile reference data
├── evidence_data2.csv               # Retail classification evidence
├── QTY_LESS_MCAT.csv                # MCATs exempt from quantity completeness scoring
│
└── scoring/                         # Scoring formulas reference + calculator
```

---

## Full Request Flow

### Single Offer Audit — Step by Step

#### Step 0 — Request arrives at the router (`app/routers/audit.py`)

The user submits a form or calls the API. All audit-triggering routes — the UI form (`POST /audit`), admin form (`POST /admin_view/audit`), and JSON API (`POST /api/audit`) — delegate to the shared `_audit_handler()`.

#### Step 1 — Fetch BuyLead data (`buylead_service.fetch_buylead_detail`)

```
GET http://dev-leads.imutils.com/wservce/buyleads/detail/
    ?modid=ETO
    &offer_type=B
    &buyer_response=2
    &additionalinfo_format=JSON
    &token=<internal token>
    &breadcrumb=1
    &offer=<offer_id>
```

The function is async (uses `httpx`). On any 5xx or timeout, it retries up to `BUYLEAD_MAX_RETRIES` times with exponential backoff. On permanent failure it raises a `RuntimeError`, which the router maps to an HTTP 502 with a user-friendly error page (`audit_error.html`).

#### Step 2 — Build the audit payload (`buylead_service.build_audit_payload_from_buylead`)

The raw BuyLead response is normalised into the `AuditPayload` schema (`app/models/schemas.py`):

| BuyLead field | Maps to | Notes |
|--------------|---------|-------|
| `ETO_OFR_TITLE` | `item_name` | |
| `ETO_OFR_DESC` | `item_desc` | |
| `MCAT_ID` | `mcat_id` | |
| `MCAT_NAME` | `mcat_name` | |
| `ETO_OFR_PRICE` | `price` | cast to float |
| `ENRICHMENTINFO` | `ISQ` | parsed via `parse_enrichmentinfo_to_isq()` |
| `ETO_ENQ_TYP` | `existing_retail_flag` | values 1,3,5,6 → `"Retail"`, else `"Non-Retail"` |

`parse_enrichmentinfo_to_isq()` deserialises the `ENRICHMENTINFO` JSON string into a canonical list of `{"IM_SPEC_MASTER_DESC": "...", "ISQ_RESPONSE": "..."}` dicts used by multiple agents.

#### Step 3 — Run 9 agents concurrently (`asyncio.gather`)

All 9 tasks launch at the same time; the slowest determines total wait time (typically the LangGraph / LLM agents, 5–15 seconds depending on LLM latency).

- **Audit API** (`auditor_service.call_auditor_api`) — POSTs the compiled `AuditPayload` to the internal categorization service; output: `specs_category_outlier_*`, `title_category_outlier_*`, `title_spec_verdict_*`.
- **Buyer Viewed Agent** — relatedness of buyer's previously enquired/viewed products.
- **Retail Agent 2** — buyer-intent classification (Retail vs Non-Retail). Early-exits to `Non-Retail` if there is no quantity in the ISQ.
- **ISQ Validation Agent** — ISQ coherence; early-exits if ISQ list is empty.
- **Description Agent** — description vs title + MCAT; early-exits if description is empty.
- **Specs / Title / Title-Specs Agent 2 trio** — LangGraph category checks; each early-exits to `Not Available` when its input is empty.
- **Buyer Profile Agent** — fetches the buyer's previous BuyLeads, previous enquiries, and user detail; runs deterministic profile-completeness + tenure checks; then an LLM genuineness judgment comparing the current BL against prior activity. The agent emits one `api_call` trace step per source fetch (endpoint, request input minus AK, raw response) ahead of its LLM step.

#### Step 4 — Post-processing: BL Scores + Completeness + Quantity

After the gather resolves, four deterministic agents run sequentially.

- **BL Score 1** (`run_scoring_agent`): weighted composite from the Audit API result, ISQ validation, description, buyer viewed, and retail2. Each agent maps to a normalised status (`correct` / `incorrect` / `na`) and confidence. N/A agents redistribute weight proportionally. Verdicts: **Approved** (≥75), **Needs Review** (30–74), **Do Not Approve** (<30). Full per-agent breakdown is saved to CSV.
- **BL Score 2** (`run_scoring_agent2`): same redistribution logic over the Agent-2 trio + ISQ, description, buyer, retail2.
- **Completeness Score** (`run_completeness_agent`): six factors (0–3 each, max 18) — title, quantity ISQ (MCAT-aware; exemptions in `QTY_LESS_MCAT.csv`), spec count, predicted-specs fill rate, buyer photos, description.
- **Quantity Audit** (`run_quantity_agent`): flags absurd quantities by five rules and returns Absurd / OK / Unverifiable + the fired rules.

#### Step 5 — Buyer activity (non-fatal)

`_resolve_buyer_activity()` reconstructs the buyer's recent timeline and (conditionally) selects activity keywords and matches searched terms — see [Buyer Activity](#buyer-activity-post-gather-non-fatal). Failures here are traced but never fail the audit.

#### Step 6 — Aggregate, log, and trace

**Tracing** (`trace_service.py`): each agent's result is added as a "step" (`name`, `type`, `started_at`, `completed_at`, `duration_ms`, `input_`, `output`, `error`). The trace is serialised to JSON and appended to `data/audit_traces.csv`. A unique `trace_id` (UUID) links the trace row to the log row.

**Logging** (`audit_log_service.py`): `append_audit_dashboard_row()` flattens all agent dicts and appends one 118-column row to `data/audit_dashboard_log.csv`. Scoring breakdowns expand into individual `s1_*` / `s2_*` columns. `_ensure_csv_headers()` auto-migrates older files by backfilling new columns.

#### Step 7 — Render the response

For the web UI, the orchestrator renders `app/templates/result.html`, which includes four sub-templates:

| Template | Content |
|----------|---------|
| `result_part1.html` | BuyLead raw data + Audit API (outlier scores) |
| `result_part2.html` | Buyer Viewed + Buyer Profile |
| `result_part3.html` | Retail Agent 2, ISQ Validation, Description |
| `result_part4.html` | Full raw trace JSON + each agent's input/output |

The result page also renders a **Quantity Audit** card and a **Buyer Activity** timeline section (with the decrypted GLID shown in the header). For the JSON API (`POST /api/audit`), the orchestrator skips rendering and returns the aggregated dict directly.

---

### Batch Audit Flow

```
1. User opens /batch  (batch.html)
   └── Uploads a CSV file (offer IDs in first column, one per row)

2. Browser opens an SSE connection to GET /batch/stream?file=<upload_path>
   └── Server reads offer IDs from the uploaded CSV
       └── For each offer_id:
             ├── Runs the full audit pipeline (same gather + scoring as single)
             ├── On success: yields SSE event  { offer_id, status: "ok", steps }
             └── On error:   yields SSE event  { offer_id, status: "error", message }
       └── After all offers: yields final summary event

3. Browser renders live progress as SSE events arrive
   └── User can download audit_dashboard_log.csv via /download/audit-dashboard-log
```

The SSE stream uses FastAPI's `StreamingResponse` with `media_type="text/event-stream"` and header `X-Accel-Buffering: no` (required for nginx). Batch and single audits share the same CSV log and traces files.

---

### RabbitMQ Consumer Flow

```
External system (e.g. BuyLead pipeline)
  │
  └── Publishes to RabbitMQ exchange → BL_AUDITOR queue
        Message body: { "args": { "ofr_id": "142764424452", "typ": 0 } }

app/consumer.py  (separate process, async aio-pika)
  │
  ├── Connects to RABBITMQ_URL, declares BL_AUDITOR queue
  ├── Sets QoS prefetch = RABBITMQ_PREFETCH
  │
  └── For each incoming message (within the daily budget + sampling):
        ├── Deserialise JSON body, extract offer_id from args.ofr_id
        ├── POST <AUDIT_API_URL>  { "offer_id": "..." }
        ├── On 2xx response: ACK the message → removed from queue
        ├── On transient error (5xx, timeout): retry up to MAX_RETRIES with backoff
        └── On permanent failure: NACK without requeue → DLX handles it
```

The consumer does not import or call any agent code — it only calls the FastAPI server. Offers beyond the daily budget (or not selected by sampling) are acked but not audited.

---

### Prompt Override Flow

Every LLM agent loads its system prompt at call time (not at startup):

```
prompt_override_service.get_active_prompt(agent_key)
  ├── Check data/prompt_overrides/{agent_key}.md → return override if present
  └── Fall back to app/{agent_folder}/prompt.md  (bundled default)
```

Prompt changes take effect on the next audit with no server restart. Overrides are written atomically by `save_override()` and deleted by `reset_override()`. The `/prompts` UI calls `POST /prompts/save` and `POST /prompts/reset`.

---

## Setup — Python (Local)

### Prerequisites

- Python 3.12+
- Access to LLM proxy (internal: `https://imllm.intermesh.net`)
- Access to the BuyLead API

### Steps

```bash
# 1. Clone and enter the project
git clone <repo-url>
cd bl-auditor-project

# 2. Create and activate a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env from template
cp .env.example .env
# Edit .env and fill in:
#   LLM_BASE_URL, LLM_API_KEY, LLM_MODEL          (required)
#   BUYER_ACTIVITY_AK                             (buyer activity timeline)
#   ACCESS_TOKEN                                  (buyer profile history APIs)
#   RABBITMQ_URL                                  (only if running the consumer)

# 5. Start the server
uvicorn main:app --reload --port 8080

# App is live at http://localhost:8080
```

> `--reload` enables hot-reload on file changes. Remove it in production.

---

## Setup — Docker

### Build and Run

```bash
# 1. Build the image
docker build -t bl-auditor .

# 2. Run with environment variables
docker run -d \
  --name bl-auditor \
  -p 8080:8080 \
  -e LLM_BASE_URL=https://imllm.intermesh.net \
  -e LLM_API_KEY=sk-your-key-here \
  -e LLM_MODEL=google/gemini-2.5-flash-lite \
  -e DATA_DIR=/app \
  -v $(pwd)/data:/app/data \
  bl-auditor
```

> The `-v $(pwd)/data:/app/data` mount persists CSV logs across container restarts. Set `DATA_DIR=/app` so files land on the mounted volume — never `/tmp`.

### Using an env file

```bash
docker run -d \
  --name bl-auditor \
  -p 8080:8080 \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  bl-auditor
```

### Docker Compose (with RabbitMQ consumer)

```yaml
# docker-compose.yml
version: "3.9"

services:
  web:
    build: .
    ports:
      - "8080:8080"
    env_file: .env
    volumes:
      - ./data:/app/data
    restart: unless-stopped

  consumer:
    build: .
    command: python -m app.consumer
    env_file: .env
    depends_on:
      - web
    restart: unless-stopped
```

```bash
docker compose up -d
```

---

## RabbitMQ Consumer

The consumer runs as a separate process. It listens on the `BL_AUDITOR` queue and forwards each message as an audit request to the FastAPI server, subject to a daily budget and sampling.

```bash
# Local (server must be running first)
python -m app.consumer
```

**Message format expected on queue:**
```json
{ "args": { "ofr_id": "142764424452", "typ": 0 } }
```

**Daily budget + sampling:** per `AUDIT_DAY_TZ` calendar day, the consumer audits up to `MAX_OFFERS` valid offers, auditing 1 of every `AUDIT_SAMPLE_EVERY`; all other offers are acked but not audited. Counters persist to `DATA_DIR/consumer_state.json` (atomic write after each audit) so the cap survives restarts/deploys.

> **Single-consumer only** — multiple consumers sharing the JSON state file would race; that case needs shared state (Redis/DB).

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_BASE_URL` | Yes | — | LLM proxy base URL |
| `LLM_API_KEY` | Yes | — | LLM API key |
| `LLM_MODEL` | Yes | — | Model name (e.g. `google/gemini-2.5-flash-lite`) |
| `LLM_TIMEOUT` | No | `60` | LLM request timeout (seconds) |
| `LLM_MAX_RETRIES` | No | `2` | Retries on transient LLM errors |
| `BUYLEAD_MAX_RETRIES` | No | `2` | Retries on BuyLead API fetch |
| `DATA_DIR` | No | project root | Directory for CSV log files (Docker: `/app`) |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| **Buyer Activity** | | | |
| `BUYER_ACTIVITY_API_BASE_URL` | No | `http://bizfeed.imutils.com` | Buyer-activity API host |
| `BUYER_ACTIVITY_AK` | For activity | — | AK JWT for the buyer-activity API (injected server-side) |
| `BUYER_ACTIVITY_MAX_RETRIES` | No | `2` | Retries on buyer-activity fetch |
| **Buyer Profile** (3 history APIs, shared AK) | | | |
| `ACCESS_TOKEN` | For profile | — | Shared AK (JWT) for all 3 buyer-profile APIs |
| `PREV_LEADS_API_BASE_URL` | No | `http://stg-leads.imutils.com` | Prev BuyLeads + prev enquiries host |
| `USER_DETAIL_API_BASE_URL` | No | `http://stg-users.imutils.com` | User detail host |
| `BUYER_PROFILE_MAX_RETRIES` | No | `2` | Retries on buyer-profile fetch |
| **RabbitMQ consumer** | | | |
| `RABBITMQ_URL` | Consumer only | — | e.g. `amqp://user:pass@host:5672/vhost` |
| `RABBITMQ_QUEUE` | No | `BL_AUDITOR` | Queue name |
| `RABBITMQ_PREFETCH` | No | `4` | QoS prefetch count |
| `AUDIT_API_URL` | No | `http://localhost:8080/audit` | FastAPI server URL (for consumer) |
| `MAX_RETRIES` | No | `3` | Consumer retry count |
| `AUDIT_HTTP_TIMEOUT` | No | `180` | Consumer HTTP timeout (seconds) |
| `MAX_OFFERS` | No | `1000` | Offers audited per calendar day |
| `AUDIT_SAMPLE_EVERY` | No | `10` | Audit 1 of every N valid offers (1 = all) |
| `AUDIT_DAY_TZ` | No | `Asia/Kolkata` | Timezone for the daily budget reset |
| **Google login** | | | |
| `GOOGLE_CLIENT_ID` | Yes | — | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Yes | — | Google OAuth client secret |
| `SESSION_SECRET` | Yes | — | Random secret; signs the session cookie |
| `ALLOWED_EMAIL_DOMAINS` | No | `indiamart.com,intermesh.net` | Comma-separated allowlist of email domains |
| `OAUTH_REDIRECT_BASE_URL` | Yes | — | Builds the `/auth/callback` URL (e.g. `https://<host>`) |
| `TRUSTED_INGEST_CIDRS` | No | `127.0.0.1/32,::1/128` | Peers allowed to `POST /audit` without login |
| `SESSION_MAX_AGE` | No | `604800` | Session lifetime in seconds (7 days) |

> All routes require Google login except `/login`, `/auth/*`, `/static/*`, `/health`, and `POST /audit` from a trusted peer IP (the consumer). See the proxy-safety note if a same-host reverse proxy fronts the app.

> The BuyLead detail URL and its token are hardcoded in `buylead_service.py` (no env var).

---

## Web UI Guide

### Pages

| URL | Description |
|-----|-------------|
| `/` | Single audit form — enter an offer ID and run a full audit |
| `/batch` | Upload a CSV of offer IDs for bulk auditing with live SSE progress |
| `/activity` | Buyer-activity timeline viewer (browse / search / enquiry / BuyLead) |
| `/records` | Filterable table of all past audits (from `audit_dashboard_log.csv`) |
| `/traces` | List of saved audit traces |
| `/traces/{trace_id}` | Step-by-step replay of a single audit trace |
| `/traces/{trace_id}/detail` | Full dashboard re-rendered from a saved trace |
| `/prompts` | Prompt editor — view/edit/reset agent prompts |
| `/admin_view` | Admin dashboard (exposes additional agent prompts/controls) |
| `/demo` | Demo result page using sample data (no API calls) |
| `/download/audit-dashboard-log` | Download full `audit_dashboard_log.csv` |
| `/download/audit-traces` | Download full `audit_traces.csv` |

### Single Audit Walkthrough

1. Open `http://localhost:8080`
2. Enter a BuyLead offer ID (e.g. `142764424452`)
3. Click **Audit** — a loading overlay appears while the 9 agents run concurrently, then scoring, completeness, and quantity agents run, then buyer activity is resolved
4. Result page shows four collapsible sections plus a Quantity Audit card and a Buyer Activity timeline:
   - **Part 1:** BuyLead raw data + Audit API (outlier scores)
   - **Part 2:** Buyer Viewed + Buyer Profile
   - **Part 3:** Retail Agent 2, ISQ Validation, Description
   - **Part 4:** Full trace JSON + raw agent inputs/outputs
5. BL Score 1, BL Score 2, and Completeness Score are displayed
6. Result is saved to `audit_dashboard_log.csv` and `audit_traces.csv`

### Buyer Activity Walkthrough

1. Open `http://localhost:8080/activity`
2. Enter a buyer `glusrId` and a `logtime` / `endlogtime` window (`YYYYMMDDhhmmss`)
3. Submit — the timeline lists browse/search/enquiry/BuyLead events with a client-side keyword filter, activity-type filter, and newest/oldest sort. The AK token is injected server-side and never appears in the URL.

### Prompt Editing

1. Open `http://localhost:8080/prompts`
2. Select an agent, edit the prompt, click **Save** — override stored in `data/prompt_overrides/{agent}.md`
3. Click **Reset** to restore the bundled default

---

## API Reference

### `POST /audit`

Run a full audit on a single offer (returns HTML).

**Request body:** `{ "offer_id": "142764424452" }`
**Response:** HTML page (rendered `result.html`)

---

### `POST /api/audit`

Returns the raw aggregated JSON audit result (no HTML rendering). Accepts an `AuditPayload` body directly.

---

### `POST /activity`

Fetch a buyer-activity timeline. Form body: `glusr_id`, `logtime`, `endlogtime` (`YYYYMMDDhhmmss`). The AK token is added server-side.

---

### `GET /batch/stream`

Server-Sent Events stream for batch audit. Pass `file` query param pointing to the uploaded CSV path.

---

### `GET /download/audit-dashboard-log` · `GET /download/audit-traces`

Download the full `audit_dashboard_log.csv` / `audit_traces.csv`.

---

## Data Storage

No database. All storage is file-based under `DATA_DIR`:

| File | Purpose | Format |
|------|---------|--------|
| `data/audit_dashboard_log.csv` | One row per audit, 118 columns | CSV, append-only |
| `data/audit_traces.csv` | Trace metadata + serialized step JSON | CSV, append-only |
| `data/prompt_overrides/*.md` | User-saved prompt overrides per agent | Markdown, auto-created |
| `data/consumer_state.json` | Consumer daily budget counters | JSON |
| `mcat_data.xlsx` | MCAT product category reference (loaded at startup) | Excel |
| `evidence_data.xlsx` | Price quartile reference by category (loaded at startup) | Excel |
| `evidence_data2.csv` | Retail classification evidence | CSV |
| `QTY_LESS_MCAT.csv` | MCATs exempt from quantity completeness scoring | CSV |

> In Docker, mount a volume at `/app/data` (and set `DATA_DIR=/app`) to persist CSV logs across restarts.

### Audit Log Columns (118 fields)

```
# Metadata
logged_at, offer_id, trace_id, item_name, item_desc, mcat_name, price,
existing_retail_flag,

# Audit API (External)
specs_category_outlier_status, specs_category_outlier_reason,
title_category_outlier_status, title_category_outlier_reason,
title_spec_verdict, title_spec_verdict_reason,

# Buyer Viewed Agent (LangGraph)
buyer_viewed_genuineness, buyer_viewed_score, buyer_viewed_confidence,
buyer_viewed_reason, buyer_viewed_product_match, buyer_viewed_error,

# Retail Agent 2 (Direct LLM)
retail_agent_2_classification, retail_agent_2_confidence,
retail_agent_2_reason, retail_agent_2_error,

# ISQ Validation Agent (Direct LLM)
isq_status, isq_score, isq_confidence, isq_reason, isq_error,

# Description Agent (Direct LLM)
desc_status, desc_score, desc_confidence, desc_reason, desc_error,

# Specs vs Category Agent 2 (LangGraph)
specs_vs_cat_a2_status, specs_vs_cat_a2_score, specs_vs_cat_a2_confidence,
specs_vs_cat_a2_reason, specs_vs_cat_a2_error,

# Title vs Category Agent 2 (LangGraph)
title_vs_cat_a2_status, title_vs_cat_a2_score, title_vs_cat_a2_confidence,
title_vs_cat_a2_reason, title_vs_cat_a2_error,

# Title vs Specs Agent 2 (LangGraph)
title_vs_specs_a2_status, title_vs_specs_a2_score, title_vs_specs_a2_confidence,
title_vs_specs_a2_reason, title_vs_specs_a2_error,

# BL Composite Scores
bl_score, bl_verdict, bl_score_2, bl_verdict_2,

# BL Score 1 — per-agent breakdown (bw=base_weight, aw=adjusted_weight, sc=score%)
s1_specs_cat_bw, s1_specs_cat_aw, s1_specs_cat_sc,
s1_title_cat_bw, s1_title_cat_aw, s1_title_cat_sc,
s1_title_specs_bw, s1_title_specs_aw, s1_title_specs_sc,
s1_isq_bw, s1_isq_aw, s1_isq_sc,
s1_buyer_bw, s1_buyer_aw, s1_buyer_sc,
s1_retail_bw, s1_retail_aw, s1_retail_sc,
s1_desc_bw, s1_desc_aw, s1_desc_sc,

# BL Score 2 — per-agent breakdown
s2_specs_cat_bw, s2_specs_cat_aw, s2_specs_cat_sc,
s2_title_cat_bw, s2_title_cat_aw, s2_title_cat_sc,
s2_title_specs_bw, s2_title_specs_aw, s2_title_specs_sc,
s2_isq_bw, s2_isq_aw, s2_isq_sc,
s2_buyer_bw, s2_buyer_aw, s2_buyer_sc,
s2_retail_bw, s2_retail_aw, s2_retail_sc,
s2_desc_bw, s2_desc_aw, s2_desc_sc,

# BL Completeness Score (6 factors × max 3 = 18)
completeness_score, completeness_pct,
cs_title, cs_quantity, cs_spec_count, cs_predicted, cs_photos, cs_desc,

# BL quality flags
bl_quality_1, bl_quality_2, bl_lq_completeness, bl_high_value,

# Buyer activity (LLM-selected keywords) + Quantity Audit (deterministic)
activity_keywords,
quantity_audit_status, quantity_audit_reason,

# Buyer Profile Agent (standalone — not part of BL Score 1/2)
buyer_profile_genuineness, buyer_profile_score, buyer_profile_confidence,
buyer_profile_reason, buyer_profile_status, buyer_profile_check_reason,
buyer_profile_tenure, buyer_profile_error
```

> **CSV columns are order-sensitive** — never reorder or remove without updating all consumers.

---

## External Services

| Service | URL | Used By |
|---------|-----|---------|
| BuyLead API | `http://dev-leads.imutils.com/wservce/buyleads/detail/` | `buylead_service` — fetches full offer data |
| Audit API (categorization) | `http://13.200.181.181/categorization_outlier` | `auditor_service` — specs/title outlier + title-spec verdict |
| Buyer Activity API | `http://bizfeed.imutils.com/ImBuyerActivity/GetData` | `buyer_activity_service` — buyer timeline |
| Prev BuyLeads / Enquiries | `<PREV_LEADS_API_BASE_URL>/wservce/rfq/display/` (`type=B` / `type=E`) | `buyer_profile_service` |
| User Detail | `<USER_DETAIL_API_BASE_URL>/wservce/users/detail/` | `buyer_profile_service` |
| LLM Proxy | `https://imllm.intermesh.net` | All LangGraph and direct LLM agents |
| RabbitMQ | configurable via `RABBITMQ_URL` | `consumer.py` — async offer queue |
