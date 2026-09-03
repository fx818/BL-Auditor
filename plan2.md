# Add buyer_profile_agent to BL-Auditor pipeline

## Context

BL-Auditor currently runs two LLM agents per audit — `retail_agent` (Reseller vs B2B classification) and `price_agent` (price reasonableness). We need a third agent — **buyer_profile_agent** — that consumes `PRODUCTS_ENQUIRED` from the BuyLead detail API along with offer_id and title, classifies the buyer, and produces a score/confidence/reason like the others. It must trace, log to CSV, and render in the result and batch dashboards exactly like the existing two.

The architecture is well-established: each agent is a self-contained LangGraph 2-node pipeline with its own `prompt.md`, env vars, and helpers. We mirror that pattern — duplicate, don't refactor.

## Output schema

```json
{
  "Display_id": "<id>",
  "Buyer_Profile_Type": "Reseller | EndUser | Trader | Wholesaler | Unknown",
  "Genuineness": "Genuine | Suspicious | Unverifiable",
  "Profile_Score": 0.0,
  "Confidence": "High | Medium | Low | None",
  "Profile_Reason": "<≤30 words>",
  "error": "<only on failure>"
}
```

Two axes: Type is the primary buyer classification (analogous to Retail's `Classification`); Genuineness is the trust/spam signal. One `Profile_Score` + one `Confidence` covers both, mirroring retail/price.

## Inputs injected into prompt

Beyond `Display_id`, `Title`, `PRODUCTS_ENQUIRED`, the prompt receives these BL fields (all from `RESPONSE.DATA`):

- **Activity**: `ETO_OFR_BUYER_TOT_REQUIREMENT`, `ETO_OFR_BUYER_TOT_UNQ_CALLS_CNT`, `ETO_OFR_BUYER_REPLY_CNT`, `ETO_OFR_BUYER_LEADS_CNT`
- **Trust**: `ETO_OFR_BUYER_IS_MOB_VERF`, `ETO_OFR_BUYER_IS_GST_VERF`, `IS_WHATSAPP_ACTIVE`, `GLUSR_USR_MEMBERSINCE`
- **Identity**: `BUSINESS_TYPE`, `GLUSR_COMPANY`, `GLUSR_CITY`, `GLUSR_STATE`
- **Category alignment**: `ETO_OFR_BUYER_PRIME_MCATS`, `ETO_OFR_BUYER_SELL_MCATS`, `ETO_OFR_BUYER_PAST_SEARCH_MCAT`

`PRODUCTS_ENQUIRED` is rendered as a JSON list (now stareach entry kept as `{FK_PC_ITEM_NAME, PRODUCT_PRICE}` — image URL dropped to save tokens).

## Files to create / modify (in order)

1. **Create** `app/buyer_profile_agent/__init__.py` — exposes `run_buyer_profile_agent`. Mirror `app/retail_agent/__init__.py`.
2. **Create** `app/buyer_profile_agent/agent.py` — full agent file. Copy `_clean`, `_parse_jsonish`, `_read_prompt`, `_render_template` verbatim from `app/retail_agent/agent.py:32-161`. Copy a slimmed-down `_build_offer_source` that ALSO extracts the new buyer signal fields (no evidence/mcat xlsx loading — buyer profile doesn't use them). Add `_extract_buyer_signals(data)` helper that returns the activity/trust/identity dict. Two-node LangGraph: `prepare_input` → `buyer_classify`. Env vars `BUYER_PROFILE_LLM_BASE_URL`, `_API_KEY`, `_MODEL`, `_TIMEOUT`. Public entry `run_buyer_profile_agent(offer_id, buylead_response, _trace=False)` returning the same shape as retail/price (`{result, agent_input, raw_output, system_prompt, user_message, sub_steps}` when `_trace=True`).
3. **Create** `app/buyer_profile_agent/prompt.md` — sections: Input, Guardrails (empty PRODUCTS_ENQUIRED + no past activity = Unverifiable hard stop), Framework (4-step: product diversity → sell activity → quantity context → genuineness), Scoring Logic (0.0–1.0 with confidence ladder), Output Format (strict JSON), Guidelines. Tone matches `app/retail_agent/prompt.md`.
4. **Modify** `app/routers/audit.py`:
   - Import: `from app.buyer_profile_agent import run_buyer_profile_agent`
   - `/audit` (~line 165): add Step 6 after Price Agent — same try/except pattern, `trace.add_step("Buyer Profile Agent", "llm_agent", ...)`, fallback dict per §"Failure fallback" below.
   - Pass `buyer_result` and `buyer_raw_json` to both `result.html` (line 230+) and `audit_error.html` (line 209+) template contexts.
   - `append_audit_dashboard_row(...)` call (line 193): add `buyer_result` 4th positional arg.
   - `/batch/stream` (~line 250): add `run_buyer_profile_agent(...)` to `asyncio.gather`, add fallback handling, append buyer keys to row dict and SSE payload.
5. **Modify** `app/services/audit_log_service.py` — append 7 keys to `CSV_HEADERS` after the price columns: `buyer_profile_type, buyer_profile_genuineness, buyer_profile_score, buyer_profile_confidence, buyer_profile_reason, buyer_profile_raw_json, buyer_profile_error`. Update `append_audit_dashboard_row` signature to accept `buyer_response: Optional[Dict[str, Any]] = None` and write those columns. `_ensure_csv_headers` handles automatic CSV migration of existing rows (already supports header changes — see lines 54-70).
6. **Modify** `app/templates/result.html`:
   - Change the `grid-2` containing Retail and Price cards (line ~107) to `grid-3` and add a third Buyer Profile card after Price. Card icon `BP`, color `--accent-purple` or similar; stat-cards for Type, Genuineness, Score, Confidence; reason at bottom; error block.
   - Add a 6th accordion "Buyer profile agent raw response" after the Price accordion (line ~181) showing `buyer_raw_json`.
7. **Modify** `app/templates/batch.html`:
   - Insert 5 `<th>` cells after Price columns: `Buyer Type`, `Genuineness`, `Profile Score`, `Profile Confidence`, `Profile Reason`, `Buyer Error`.
   - Update `appendRow` JS to render those cells from `d.buyer_profile_type`, `d.buyer_profile_genuineness`, `d.buyer_profile_score`, `d.buyer_profile_confidence`, `d.buyer_profile_reason`, `d.buyer_profile_error`.
   - Update colspan in error row from `28` to match the new total column count.

`audit_error.html` and `trace_detail.html` need **no changes** — they iterate steps generically.

## Failure fallback dict (in audit.py)

```python
buyer_result = {
    "Display_id": offer_id,
    "Buyer_Profile_Type": "Unknown",
    "Genuineness": "Unverifiable",
    "Profile_Score": None,
    "Confidence": "None",
    "Profile_Reason": "Buyer profile classification failed; see buyer_error.",
    "error": str(buyer_exc),
}
```

## Reuse decisions (explicit)

- **Duplicate, don't extract.** Copy `_clean`, `_parse_jsonish`, `_read_prompt`, `_render_template` from `app/retail_agent/agent.py` into `app/buyer_profile_agent/agent.py`. Reason: retail and price already duplicate them; lifting now would touch three files for zero functional benefit and grow this PR's blast radius. Defer the `app/agents/common.py` consolidation to a separate refactor task.
- **Skip evidence_data.xlsx and mcat_data.xlsx.** Buyer profile reasons over BL response signals only. No price quantiles, no slab buckets.
- **Reuse trace plumbing.** `trace.add_step` and `audit_error.html` / `trace_detail.html` are generic — buyer profile slots in as Step 6 with zero template changes.

## Verification

After implementation, in order:

1. **Smoke test single audit (success)**: `POST /audit` with a known offer_id; verify result.html shows three cards (Retail, Price, Buyer Profile) and three raw-response accordions, the trace JSON in `audit_traces/` has 6 steps, the CSV has the 7 new columns populated.
2. **Smoke test single audit (failure)**: temporarily unset `BUYER_PROFILE_LLM_API_KEY`; the buyer agent should throw, fallback dict should render in the card with the error block, the audit_error.html path is NOT triggered (buyer_profile failure is non-fatal — same as retail/price). The full audit completes.
3. **Smoke test BL API failure**: use an invalid offer_id that 404s; audit_error.html should render and step list should NOT include buyer_profile (it never ran). Confirms the new step doesn't break the error template.
4. **Batch test**: run `/batch` with 2–3 offer IDs, confirm new columns appear and parallel `asyncio.gather` includes 4 calls now (auditor + 3 agents), error rows show updated colspan.
5. **CSV migration**: open `audit_dashboard_log.csv` in editor — old rows should have empty strings in the new buyer_profile columns (handled by `_ensure_csv_headers`).
6. **Trace UI**: navigate to `/traces/{trace_id}`, confirm Step 6 "Buyer Profile Agent" renders with system_prompt, user_message, raw_output, parsed result, and sub_steps accordions — all auto-rendered by the generic template.

## Critical files for implementation

- `C:\Users\Imart\Documents\GitHub\BL-Auditor\app\buyer_profile_agent\agent.py` (new)
- `C:\Users\Imart\Documents\GitHub\BL-Auditor\app\buyer_profile_agent\prompt.md` (new)
- `C:\Users\Imart\Documents\GitHub\BL-Auditor\app\buyer_profile_agent\__init__.py` (new)
- `C:\Users\Imart\Documents\GitHub\BL-Auditor\app\routers\audit.py` (Step 6 + batch + template ctx)
- `C:\Users\Imart\Documents\GitHub\BL-Auditor\app\services\audit_log_service.py` (CSV headers + signature)
- `C:\Users\Imart\Documents\GitHub\BL-Auditor\app\templates\result.html` (grid-3 + accordion)
- `C:\Users\Imart\Documents\GitHub\BL-Auditor\app\templates\batch.html` (table cols + appendRow)
