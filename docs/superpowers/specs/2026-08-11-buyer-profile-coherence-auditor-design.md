# BuyerProfile Agent → BL-Title Coherence Auditor

**Date:** 2026-08-11
**Status:** Approved (design)

## Summary

Repurpose the `buyer_profile_agent` from a buyer *genuineness / profile-completeness*
checker into a **BL-title coherence auditor**. The LLM now decides whether the current
BuyLead title is coherent (`related` / `not related`) with the buyer's history **and** a
new third evidence source: the buyer's recent **activity log (CSL)**.

The deterministic profile-completeness + tenure signals are removed. Score, Confidence,
Genuineness, and matched_signal are removed. The output schema collapses to two fields.

## Motivation

The current agent judges "genuineness" from prev BuyLeads + prev enquiries + a
deterministic profile check. We want a sharper, single-purpose signal: does the incoming
BL title actually match what this buyer has recently searched, browsed, enquired about, or
posted? The buyer activity log (already fetched elsewhere in the pipeline but never fed to
this LLM) is the strongest evidence for that and must be included.

## Scope of change

### 1. Agent core (`app/buyer_profile_agent/agent.py`)

- **Signature:** `run_buyer_profile_agent(offer_id, buylead_response, buyer_activity=None, _trace=False)`.
  Drop the `user_detail` parameter.
- **LLM inputs:** `current_bl` (title/desc + supporting counts) + `prev_buyleads` +
  `prev_enquiries` + **`buyer_activity`** (trimmed CSL).
- **CSL trimming:** pass at most the **30 most recent** activity events, each as
  `{time_label, activity_type, keyword, city, mcat_name}`, plus the activity `summary` and
  `type_counts`.
- **Remove** the deterministic block: `extract_user_profile`, `evaluate_profile_completeness`,
  `evaluate_tenure` calls and their imports. No User Detail fetch inside this agent.
- **Output parsing:** expect strict JSON `{status, reasoning}`.
  - `status` ∈ {`related`, `not related`}, default `not related`.
  - `reasoning`: 1–3 sentence justification.
- **Guardrail:** if `prev_buyleads` **and** `prev_enquiries` **and** activity events are all
  empty → return `status="not related"`,
  `reasoning="No prior activity or buylead history available to assess relatedness."`
  **without** calling the LLM.

**New result dict:**
```
{Display_id, status, reasoning, Glid, prev_buyleads, prev_enquiries, error?}
```
With `_trace=True`, also returns `agent_input, system_prompt, user_message, raw_output, api_calls`.

### 2. Prompt (`app/buyer_profile_agent/prompt.md`)

Replace entirely with the "BL Coherence Auditor" prompt. It receives `current_bl`,
`prev_buyleads`, `prev_enquiries`, and `buyer_activity` and returns strict JSON
`{status, reasoning}`. Overridable via `get_active_prompt("buyer_profile")` (key unchanged).

### 3. Orchestration reorder (`app/routers/audit.py`)

Currently `run_buyer_profile_agent` runs in the early `asyncio.gather`; buyer activity is
fetched later in `_resolve_buyer_activity`. To reuse the activity (no duplicate API call):

- **Split `_resolve_buyer_activity`** into:
  - `_fetch_buyer_activity_for_bl(buylead_response, trace)` → decrypt glid, compute window,
    fetch + parse activity, add the `"Buyer Activity"` trace step. Returns `(buyer_activity, buyer_glid)`.
    Runs **before** the gather (next to the existing `user_detail` fetch; may be parallelized with it).
  - `_enrich_activity_keywords(buyer_activity, buyer_glid, payload, desc_result, trace)` → the
    desc-dependent Activity-Keywords + Searched-Terms-Match logic only. Runs **after** the gather.
- Pass `buyer_activity` into `run_buyer_profile_agent(...)` inside the gather.
- Apply to **both** the single `_audit_handler` and the streaming/batch handler.

### 4. CSV schema (`app/services/audit_log_service.py`)

Replace the 8 buyer_profile columns
(`buyer_profile_genuineness/score/confidence/reason/status/check_reason/tenure/error`) with
**3**: `buyer_profile_status`, `buyer_profile_reasoning`, `buyer_profile_error`.
`buyer_profile_status` now holds `related`/`not related`.

**Migration:** archive the existing `audit_dashboard_log.csv` (rename → `.bak`) so a fresh
file is written with the new header. CSV column order is order-sensitive per CLAUDE.md.

### 5. UI (`app/templates/result.html`, ~536–550)

Replace the buyer_profile stat cards (Genuineness/Score/Confidence/Tenure/Profile_Check_Reason)
with a single **Status** card (`related` → green, `not related` → red) + a **reasoning** text
block. The `br` block (~502–511, `buyer_viewed_agent`) and `batch.html` are **untouched**.

### 6. Trace fallbacks (`app/routers/audit.py`)

Update `_BUYER_PROFILE_FAIL` and the inline buyer_profile failure dicts (single + streaming)
to `{status:"not related", reasoning:<msg>, error:<...>}`.

## Non-goals / untouched

- `buyer_viewed_agent` (separate agent, similar field names — do not touch).
- `batch.html` (does not render buyer_profile output).
- ISQ agent's User Detail fetch (still needed; unchanged).
- Buyer-profile scoring weight key (already bound to buyer_viewed; not affected).
- Langfuse callback wiring (agent uses ChatOpenAI directly today; leave as-is).

## Error handling

Every failure degrades to `status="not related"` with an explanatory `reasoning` and an
`error` field set. The agent never raises into the audit. Activity fetch failure → agent
runs on history alone (activity absent).

## Defaults chosen

- Activity events passed to LLM capped at 30 most recent.
- Old CSV archived rather than migrated in place.
