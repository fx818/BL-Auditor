# BuyerProfile → BL-Title Coherence Auditor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repurpose `buyer_profile_agent` into a BL-title coherence auditor that feeds buyer activity (CSL) into the LLM alongside prev BuyLeads/enquiries and outputs `{status, reasoning}`.

**Architecture:** The agent drops its deterministic profile/tenure block and User-Detail dependency. Buyer activity is fetched once in the orchestration (reusing the existing `buyer_activity_service`) and passed into the agent, so no duplicate API call. Output schema, CSV columns, UI card, and trace fallbacks all collapse to `status` + `reasoning`.

**Tech Stack:** FastAPI, Jinja2, LangChain `ChatOpenAI`, httpx, append-only CSV. Python 3.11+ async.

## Global Constraints

- **No test suite / no lint config exists.** Verification is via `python -c` import checks, small inline `python` assertion snippets for pure functions, and app-boot import checks. Do NOT introduce pytest.
- **CSV columns are order-sensitive** — never reorder/remove without updating all consumers (this plan does, deliberately, and archives the old file).
- **LangGraph pinned to 0.2.x; Pydantic v2.** (Not directly touched here.)
- **Windows + PowerShell** shell; a Bash tool is also available for POSIX snippets.
- Prompt override key stays `"buyer_profile"`; the new prompt is a **static system prompt** (no `{{...}}` placeholders) — evidence goes in the user message as JSON.
- The agent must **never raise** into the audit — every failure degrades to `status="not related"` + explanatory `reasoning` + `error`.
- Do **not** touch `buyer_viewed_agent`, `batch.html`, the ISQ User-Detail fetch, or Langfuse wiring.

---

## File Structure

- `app/buyer_profile_agent/prompt.md` — **replace**: new static coherence-auditor prompt.
- `app/buyer_profile_agent/agent.py` — **rewrite**: new signature, CSL trimming, `{status,reasoning}` output, guardrail.
- `app/routers/audit.py` — **modify**: split activity resolution, fetch activity pre-gather, pass into agent, update failure dicts + `_BUYER_PROFILE_FAIL` (both single + streaming handlers).
- `app/services/audit_log_service.py` — **modify**: swap 8 buyer_profile columns for 3 + update row mapping.
- `app/templates/result.html` — **modify**: buyer_profile card (lines ~537–550).
- `audit_dashboard_log.csv` — **archive** to `.bak` (schema change).

---

### Task 1: Rewrite prompt + agent core

**Files:**
- Modify (replace): `app/buyer_profile_agent/prompt.md`
- Modify (rewrite): `app/buyer_profile_agent/agent.py`

**Interfaces:**
- Consumes: `app.services.buyer_profile_service.{fetch_prev_buyleads, fetch_prev_enquiries, extract_prev_bls, extract_prev_enqs, LEADS_API_URL}`; `app.services.glid_crypto_service.decrypt_glid`; `app.services.prompt_override_service.get_active_prompt`.
- Produces: `run_buyer_profile_agent(offer_id: str, buylead_response: dict, buyer_activity: dict | None = None, _trace: bool = False) -> dict`. Result dict keys: `Display_id, status, reasoning, Glid, prev_buyleads, prev_enquiries, error?`. With `_trace=True`: wrapped as `{result, agent_input, raw_output, system_prompt, user_message, api_calls}`. New module-level pure helpers used by later steps' verification: `_trim_activity(buyer_activity, limit=30) -> dict`, `_normalize_output(parsed) -> dict`, `_has_activity_events(buyer_activity) -> bool`.

- [ ] **Step 1: Replace the prompt**

Overwrite `app/buyer_profile_agent/prompt.md` with this exact content:

```markdown
You are a Buylead (BL) Coherence Auditor for IndiaMART's buyer-intelligence pipeline.

Your job: given a single incoming Buylead's title for a specific buyer (glid), decide whether it is
genuinely coherent with what is already known about that buyer, using two evidence sources supplied
in the user message:

1. Buyer history - prev_buyleads and prev_enquiries: titles/descriptions of buyleads and enquiries this
   buyer has raised in the past, each with a posting date.
2. Buyer activity log (CSL) - a time-ordered log of the buyer's own recent Search, Browse, and ENQ
   (enquiry) events, each with a keyword, city, and timestamp, plus a summary of counts by type.

Classify the current BL title as "related" when:
- It shares the same product/category, brand, or a close synonym with one or more items in the
  activity log or buyer history (e.g. "Havells Led Light 15 Watt" matches a "Havells LED Light 15 Watt"
  search/browse event) - case, spacing, and unit-formatting differences are NOT mismatches.
- It is a natural continuation of a browse -> search -> enquiry funnel visible in the activity log,
  even if worded slightly differently from any single logged event.
- The city/location on the BL is consistent with the buyer's recent activity.

Classify the current BL title as "not related" when:
- It describes a materially different product/category than everything in the activity log and
  history, with no plausible connection (e.g. a BL for "Wash Basins" when all recent activity concerns
  switches and lighting).
- It has no supporting search, browse, or enquiry evidence anywhere in the provided data.
- The available evidence is genuinely too thin or contradictory to support a "related" call - in
  ambiguous cases, prefer "not related" and say so plainly in your reasoning rather than guessing.

Rules:
- Base your judgment only on the data provided in the user message. Never assume outside knowledge
  about the buyer or invent evidence that isn't there.
- Ignore superficial formatting differences (case, spacing, abbreviations like "15W" vs "15 Watt").
- Always respond using only the required structured output fields - no extra commentary, no markdown,
  no text outside the schema.

## Output — strict JSON only

Return exactly this JSON object and nothing else:

```json
{
  "status": "related | not related",
  "reasoning": "1-3 sentence justification citing the specific matching or conflicting evidence."
}
```
```

- [ ] **Step 2: Rewrite `agent.py`**

Overwrite `app/buyer_profile_agent/agent.py` with:

```python
"""Buyer Profile Agent — BL-title coherence auditor (standalone).

Given the current BuyLead and the buyer's recent evidence — prev BuyLeads (≤10),
prev enquiries (≤10), and the buyer activity log (CSL) — an LLM decides whether the
current BL title is coherent with that history: ``related`` or ``not related``.

Buyer activity is fetched once by the orchestration and passed in via ``buyer_activity``
(the parsed dict from ``buyer_activity_service.parse_buyer_activity``); the agent does
not fetch it. Any source failure degrades gracefully — the agent never raises.

Result dict:
    {
        "Display_id": str,
        "status": "related" | "not related",
        "reasoning": str,
        "Glid": str,
        "prev_buyleads": list,
        "prev_enquiries": list,
        "error": str   # only when something failed
    }
"""
import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.services.glid_crypto_service import decrypt_glid
from app.services.buyer_profile_service import (
    LEADS_API_URL,
    extract_prev_bls,
    extract_prev_enqs,
    fetch_prev_buyleads,
    fetch_prev_enquiries,
)

log = logging.getLogger("bl-auditor.buyer_profile_agent")
_LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
_ACTIVITY_EVENT_LIMIT = 30

_NO_HISTORY_REASON = "No prior activity or buylead history available to assess relatedness."


def _read_prompt() -> str:
    from app.services.prompt_override_service import get_active_prompt
    return get_active_prompt("buyer_profile")[0]


def _clean_classifier_output(raw: str) -> Dict[str, Any]:
    raw = (raw or "").replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end >= start:
            try:
                return json.loads(raw[start:end + 1].replace("\n", " ").replace("\t", " "))
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON parse failed: {exc.msg}. Raw: {raw[:300]!r}") from exc
        raise ValueError(f"No JSON object found. Raw: {raw[:300]!r}")


def _normalize_output(parsed: Dict[str, Any]) -> Dict[str, str]:
    """Coerce the LLM output to the strict {status, reasoning} schema."""
    status = str((parsed or {}).get("status", "")).strip().lower()
    if status not in ("related", "not related"):
        status = "not related"
    reasoning = str((parsed or {}).get("reasoning", "")).strip()
    return {"status": status, "reasoning": reasoning}


def _has_activity_events(buyer_activity: Dict[str, Any] | None) -> bool:
    return bool(buyer_activity and buyer_activity.get("ok") and buyer_activity.get("events"))


def _trim_activity(buyer_activity: Dict[str, Any] | None, limit: int = _ACTIVITY_EVENT_LIMIT) -> Dict[str, Any]:
    """Compact the parsed activity to what the LLM needs: newest ``limit`` events
    (keyword/city/type/time/mcat) plus the summary and per-type counts."""
    ba = buyer_activity or {}
    events = ba.get("events") or []
    trimmed = [
        {
            "time_label": e.get("time_label"),
            "activity_type": e.get("activity_type"),
            "keyword": e.get("keyword"),
            "city": e.get("city"),
            "mcat_name": e.get("mcat_name"),
        }
        for e in events[:limit]
        if isinstance(e, dict)
    ]
    return {
        "summary": ba.get("summary", {}),
        "type_counts": ba.get("type_counts", {}),
        "events": trimmed,
    }


def _build_current_bl(offer_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "Display_id": str(data.get("ETO_OFR_DISPLAY_ID") or offer_id),
        "title": str(data.get("ETO_OFR_TITLE") or "").strip(),
        "desc": str(data.get("ETO_OFR_DESC") or "").strip(),
        "tot_requirement": data.get("ETO_OFR_BUYER_TOT_REQUIREMENT"),
        "unq_calls_cnt": data.get("ETO_OFR_BUYER_TOT_UNQ_CALLS_CNT"),
        "reply_cnt": data.get("ETO_OFR_BUYER_REPLY_CNT"),
        "prime_mcats": data.get("ETO_OFR_BUYER_PRIME_MCATS"),
    }


async def _judge_coherence(
    current_bl: Dict[str, Any],
    prev_bls: List[Dict[str, str]],
    prev_enqs: List[Dict[str, str]],
    activity: Dict[str, Any],
) -> Dict[str, Any]:
    """Call the LLM to judge coherence. Returns {parsed, system_prompt, user_message, raw_output}."""
    base_url = os.getenv("LLM_BASE_URL")
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    timeout = float(os.getenv("LLM_TIMEOUT", "60"))

    if not api_key or not model:
        raise RuntimeError("Missing LLM_API_KEY or LLM_MODEL")

    system_prompt = _read_prompt()
    user_text = "\n".join([
        f"current_bl: {json.dumps(current_bl, ensure_ascii=False)}",
        f"prev_buyleads: {json.dumps(prev_bls, ensure_ascii=False)}",
        f"prev_enquiries: {json.dumps(prev_enqs, ensure_ascii=False)}",
        f"buyer_activity: {json.dumps(activity, ensure_ascii=False)}",
    ])

    llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url, timeout=timeout)

    last_exc: Exception | None = None
    for attempt in range(1, _LLM_MAX_RETRIES + 2):
        try:
            response = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_text)])
            break
        except Exception as exc:
            last_exc = exc
            log.warning(
                "buyer_profile LLM attempt %d/%d failed: %s: %s",
                attempt, _LLM_MAX_RETRIES + 1, type(exc).__name__, exc,
            )
            if attempt <= _LLM_MAX_RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))
    else:
        raise RuntimeError(
            f"Buyer profile LLM exhausted {_LLM_MAX_RETRIES + 1} attempts: {last_exc}"
        ) from last_exc

    raw_output = str(response.content)
    try:
        parsed = _clean_classifier_output(raw_output)
    except ValueError as exc:
        raise RuntimeError(f"Buyer profile LLM parse failed: {exc}") from exc

    return {"parsed": parsed, "system_prompt": system_prompt, "user_message": user_text, "raw_output": raw_output}


async def _timed_fetch(coro, *, name: str, endpoint: str, input_: Dict[str, Any]):
    """Await a source fetch, capturing a trace-ready record (request in/out, timing).

    Never raises — a failure is recorded with `error` set and `output` None so the
    agent degrades gracefully. The AK token is intentionally NOT in `input_`.
    """
    t = time.monotonic()
    try:
        out = await coro
        return {"name": name, "endpoint": endpoint, "input": input_, "output": out,
                "error": "", "duration_ms": int((time.monotonic() - t) * 1000)}
    except Exception as exc:
        log.warning("buyer_profile %s fetch failed: %s: %s", name, type(exc).__name__, exc)
        return {"name": name, "endpoint": endpoint, "input": input_, "output": None,
                "error": str(exc), "duration_ms": int((time.monotonic() - t) * 1000)}


async def run_buyer_profile_agent(
    offer_id: str,
    buylead_response: Dict[str, Any],
    buyer_activity: Dict[str, Any] | None = None,
    _trace: bool = False,
) -> Dict[str, Any]:
    data = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {}) or {}
    display_id = str(data.get("ETO_OFR_DISPLAY_ID") or offer_id)

    result: Dict[str, Any] = {
        "Display_id": display_id,
        "status": "not related",
        "reasoning": "",
        "Glid": "",
        "prev_buyleads": [],
        "prev_enquiries": [],
    }
    agent_input: Dict[str, Any] = {}
    system_prompt = ""
    user_message = ""
    raw_output = ""

    enc_glid = data.get("FK_GLUSR_USR_ID")
    if not enc_glid:
        result["reasoning"] = "No buyer GLID available; cannot assess relatedness."
        result["error"] = "missing FK_GLUSR_USR_ID"
        return _finalize(result, agent_input, raw_output, system_prompt, user_message, _trace)

    try:
        glid = decrypt_glid(str(enc_glid))
    except Exception as exc:
        result["reasoning"] = "GLID decryption failed; cannot assess relatedness."
        result["error"] = f"glid decrypt failed: {exc}"
        return _finalize(result, agent_input, raw_output, system_prompt, user_message, _trace)

    result["Glid"] = glid

    prev_bl_rec, prev_enq_rec = await asyncio.gather(
        _timed_fetch(fetch_prev_buyleads(glid), name="Prev BuyLeads API",
                     endpoint=LEADS_API_URL, input_={"glusrid": glid, "type": "B", "latest_lead": 10}),
        _timed_fetch(fetch_prev_enquiries(glid), name="Prev Enquiries API",
                     endpoint=LEADS_API_URL, input_={"glusrid": glid, "type": "E", "latest_lead": 10}),
    )
    api_calls = [prev_bl_rec, prev_enq_rec]

    prev_bls = extract_prev_bls(prev_bl_rec["output"] or {})
    prev_enqs = extract_prev_enqs(prev_enq_rec["output"] or {})
    result["prev_buyleads"] = prev_bls
    result["prev_enquiries"] = prev_enqs

    current_bl = _build_current_bl(offer_id, data)
    activity = _trim_activity(buyer_activity)
    agent_input = {
        "glid": glid,
        "current_bl": current_bl,
        "prev_buyleads": prev_bls,
        "prev_enquiries": prev_enqs,
        "buyer_activity": activity,
    }

    if not prev_bls and not prev_enqs and not _has_activity_events(buyer_activity):
        result["status"] = "not related"
        result["reasoning"] = _NO_HISTORY_REASON
        return _finalize(result, agent_input, raw_output, system_prompt, user_message, _trace, api_calls)

    try:
        judged = await _judge_coherence(current_bl, prev_bls, prev_enqs, activity)
    except Exception as exc:
        result["status"] = "not related"
        result["reasoning"] = "Coherence LLM failed; see error."
        result["error"] = str(exc)
        return _finalize(result, agent_input, raw_output, system_prompt, user_message, _trace, api_calls)

    system_prompt = judged["system_prompt"]
    user_message = judged["user_message"]
    raw_output = judged["raw_output"]

    normalized = _normalize_output(judged["parsed"])
    result["status"] = normalized["status"]
    result["reasoning"] = normalized["reasoning"]

    return _finalize(result, agent_input, raw_output, system_prompt, user_message, _trace, api_calls)


def _finalize(result, agent_input, raw_output, system_prompt, user_message, _trace, api_calls=None):
    if not _trace:
        return result
    return {
        "result": result,
        "agent_input": agent_input,
        "raw_output": raw_output,
        "system_prompt": system_prompt,
        "user_message": user_message,
        "api_calls": api_calls or [],
    }
```

- [ ] **Step 3: Verify module imports cleanly**

Run:
```bash
python -c "from app.buyer_profile_agent.agent import run_buyer_profile_agent, _trim_activity, _normalize_output, _has_activity_events; print('ok')"
```
Expected: prints `ok`, no ImportError/SyntaxError.

- [ ] **Step 4: Verify the pure helpers (offline, no network/LLM)**

Run (Bash tool):
```bash
python - <<'PY'
from app.buyer_profile_agent.agent import _trim_activity, _normalize_output, _has_activity_events

# _normalize_output coerces unknown/garbage status to "not related"
assert _normalize_output({"status": "related", "reasoning": "x"}) == {"status": "related", "reasoning": "x"}
assert _normalize_output({"status": "RELATED", "reasoning": " y "}) == {"status": "related", "reasoning": "y"}
assert _normalize_output({"status": "maybe"})["status"] == "not related"
assert _normalize_output({})["status"] == "not related"

# _has_activity_events only true when ok + non-empty events
assert _has_activity_events({"ok": True, "events": [{"a": 1}]}) is True
assert _has_activity_events({"ok": True, "events": []}) is False
assert _has_activity_events({"ok": False, "events": [{"a": 1}]}) is False
assert _has_activity_events(None) is False

# _trim_activity caps events and keeps only the 5 fields + summary/type_counts
ba = {"ok": True, "summary": {"total": 40}, "type_counts": {"BL": 1},
      "events": [{"time_label": str(i), "activity_type": "BL", "keyword": "k",
                  "city": "Delhi", "mcat_name": "m", "extra": "drop"} for i in range(40)]}
t = _trim_activity(ba)
assert len(t["events"]) == 30
assert set(t["events"][0].keys()) == {"time_label", "activity_type", "keyword", "city", "mcat_name"}
assert t["summary"] == {"total": 40} and t["type_counts"] == {"BL": 1}
assert _trim_activity(None) == {"summary": {}, "type_counts": {}, "events": []}
print("pure-helper checks passed")
PY
```
Expected: prints `pure-helper checks passed`.

- [ ] **Step 5: Verify the no-GLID guardrail path (offline)**

Run (Bash tool):
```bash
python - <<'PY'
import asyncio
from app.buyer_profile_agent.agent import run_buyer_profile_agent

# BuyLead with no FK_GLUSR_USR_ID -> returns immediately, no network, status not related
r = asyncio.run(run_buyer_profile_agent("OFR1", {"RESPONSE": {"DATA": {"ETO_OFR_DISPLAY_ID": "D1"}}}))
assert r["status"] == "not related", r
assert r["error"] == "missing FK_GLUSR_USR_ID", r
assert r["Display_id"] == "D1", r
assert "reasoning" in r and r["reasoning"]
print("guardrail check passed")
PY
```
Expected: prints `guardrail check passed`.

- [ ] **Step 6: Commit**

```bash
git add app/buyer_profile_agent/prompt.md app/buyer_profile_agent/agent.py
git commit -m "feat(buyer_profile): rewrite as BL-title coherence auditor with activity input"
```

---

### Task 2: Orchestration — reuse activity + update failure dicts

**Files:**
- Modify: `app/routers/audit.py`

**Interfaces:**
- Consumes: `run_buyer_profile_agent(..., buyer_activity=..., _trace=True)` (Task 1); existing `window_from_bl_data`, `fetch_buyer_activity`, `parse_buyer_activity`, `BUYER_ACTIVITY_API_URL`, `BUYER_ACTIVITY_AK`, `decrypt_glid`, `select_activity_keywords`, `match_searched_terms`.
- Produces: `_fetch_buyer_activity_for_bl(buylead_response, trace) -> (buyer_activity, buyer_glid)` and `_enrich_activity_keywords(buyer_activity, buylead_response, payload, desc_result, trace) -> (activity_kw, term_match)`; replaces `_resolve_buyer_activity`.

- [ ] **Step 1: Replace `_resolve_buyer_activity` with two helpers**

Replace the entire `_resolve_buyer_activity` function (currently lines ~162–225) with:

```python
async def _fetch_buyer_activity_for_bl(buylead_response, trace):
    """Fetch + parse the buyer's activity for the BL window and add the "Buyer
    Activity" trace step. Returns (buyer_activity, buyer_glid). Non-fatal — any
    failure records the trace step and returns (None, buyer_glid_or_None)."""
    data = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {}) or {}
    enc_glid = data.get("FK_GLUSR_USR_ID")
    if not enc_glid:
        return None, None
    buyer_activity = None
    buyer_glid = None
    logt, endt = window_from_bl_data(data)
    t_ba = time.monotonic()
    try:
        buyer_glid = decrypt_glid(str(enc_glid))
        ba_raw = await fetch_buyer_activity(
            glusr_id=buyer_glid, ak=BUYER_ACTIVITY_AK, logtime=logt, endlogtime=endt,
        )
        buyer_activity = parse_buyer_activity(ba_raw, glusr_id=buyer_glid)
        trace.add_step("Buyer Activity", "api_call", endpoint=BUYER_ACTIVITY_API_URL,
            input_={"glusrId": buyer_glid, "logtime": logt, "endlogtime": endt},
            output=ba_raw, duration_ms=int((time.monotonic() - t_ba) * 1000))
    except Exception as _ba_exc:
        trace.add_step("Buyer Activity", "api_call", endpoint=BUYER_ACTIVITY_API_URL,
            input_={"encrypted_glid": str(enc_glid), "decrypted_glid": buyer_glid,
                    "logtime": logt, "endlogtime": endt},
            error=_ba_exc, duration_ms=int((time.monotonic() - t_ba) * 1000))
    return buyer_activity, buyer_glid


async def _enrich_activity_keywords(buyer_activity, buylead_response, payload, desc_result, trace):
    """Desc-dependent enrichment: pick activity keywords + match searched terms.

    Only runs when the description starts with "Buyer Searched for" AND the
    description verdict is Incorrect AND activity is available. Non-fatal.
    Returns (activity_kw, term_match)."""
    activity_kw = None
    term_match = None
    data = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {}) or {}
    desc_text = str(data.get("ETO_OFR_DESC") or payload.get("item_desc") or "").strip()
    desc_status = (desc_result or {}).get("Status", "")
    if (desc_text.lower().startswith("buyer searched for")
            and desc_status == "Incorrect"
            and buyer_activity and buyer_activity.get("ok")):
        t_kw = time.monotonic()
        try:
            activity_kw = await select_activity_keywords(payload.get("mcat_name", ""), buyer_activity)
            trace.add_step("Activity Keywords", "llm_agent",
                input_={"mcat_name": payload.get("mcat_name", ""), "candidates": activity_kw.get("candidates", [])},
                parsed=activity_kw, duration_ms=int((time.monotonic() - t_kw) * 1000))
        except Exception as _kw_exc:
            activity_kw = {"keywords": [], "candidates": [], "reason": "", "error": str(_kw_exc)}
            trace.add_step("Activity Keywords", "llm_agent",
                error=_kw_exc, duration_ms=int((time.monotonic() - t_kw) * 1000))

        # Deterministic (no LLM): split the "Buyer searched for X, Y, Z" desc into
        # terms and report, per term, whether it appears in the activity log.
        t_tm = time.monotonic()
        try:
            term_match = match_searched_terms(desc_text, buyer_activity)
            trace.add_step("Searched Terms Match", "transform",
                input_={"terms": [t["term"] for t in term_match["terms"]],
                        "log_keywords": term_match["log_keywords"]},
                parsed=term_match, duration_ms=int((time.monotonic() - t_tm) * 1000))
        except Exception as _tm_exc:
            trace.add_step("Searched Terms Match", "transform",
                error=_tm_exc, duration_ms=int((time.monotonic() - t_tm) * 1000))
    return activity_kw, term_match
```

- [ ] **Step 2: Single handler — fetch activity before the gather**

In `_audit_handler`, right after the `user_detail = await fetch_user_detail_for_buylead(buylead_response)` line (~309), add:

```python
            # Buyer activity fetched once (reused by the buyer-profile agent below
            # and the desc-keyword enrichment after the gather). Non-fatal.
            buyer_activity, buyer_glid = await _fetch_buyer_activity_for_bl(buylead_response, trace)
```

- [ ] **Step 3: Single handler — pass activity into the agent**

In the same `asyncio.gather` (~325), change the buyer-profile line from:
```python
                _timed(run_buyer_profile_agent(offer_id, buylead_response, user_detail=user_detail, _trace=True)),
```
to:
```python
                _timed(run_buyer_profile_agent(offer_id, buylead_response, buyer_activity=buyer_activity, _trace=True)),
```

- [ ] **Step 4: Single handler — replace the post-gather activity call**

Replace the block (~642–646):
```python
            # Step 16: Buyer Activity + activity-keyword selection (non-fatal).
            buyer_activity_result, buyer_glid, activity_kw_result, searched_terms_match = await _resolve_buyer_activity(
                buylead_response, payload, desc_result, trace,
            )
            activity_keywords = (activity_kw_result or {}).get("keywords") or []
```
with:
```python
            # Step 16: desc-dependent activity-keyword enrichment (activity already
            # fetched pre-gather). Non-fatal.
            activity_kw_result, searched_terms_match = await _enrich_activity_keywords(
                buyer_activity, buylead_response, payload, desc_result, trace,
            )
            buyer_activity_result = buyer_activity
            activity_keywords = (activity_kw_result or {}).get("keywords") or []
```

- [ ] **Step 5: Single handler — update the buyer-profile failure dict**

Replace the failure dict (~572–583):
```python
                buyer_profile_result = {
                    "Display_id": offer_id,
                    "Genuineness": "Not Available",
                    "Score": None,
                    "Confidence": "None",
                    "Reason": "Buyer Profile Agent failed; see error.",
                    "Profile_Status": "Incomplete",
                    "Profile_Check_Reason": "Buyer Profile Agent failed; see error.",
                    "Tenure": "Unknown",
                    "Glid": "",
                    "error": str(buyer_profile_exc),
                }
```
with:
```python
                buyer_profile_result = {
                    "Display_id": offer_id,
                    "status": "not related",
                    "reasoning": "Buyer Profile Agent failed; see error.",
                    "Glid": "",
                    "error": str(buyer_profile_exc),
                }
```

- [ ] **Step 6: Update `_BUYER_PROFILE_FAIL`**

Replace (~809–814):
```python
_BUYER_PROFILE_FAIL = {
    "Genuineness": "Not Available", "Score": None, "Confidence": "None",
    "Reason": "Buyer Profile Agent did not run for this trace.",
    "Profile_Status": "Incomplete", "Profile_Check_Reason": "Not available for this trace.",
    "Tenure": "Unknown", "Glid": "",
}
```
with:
```python
_BUYER_PROFILE_FAIL = {
    "status": "not related",
    "reasoning": "Buyer Profile Agent did not run for this trace.",
    "Glid": "",
}
```

- [ ] **Step 7: Streaming handler — fetch activity before the gather**

In the streaming handler, right after `user_detail = await fetch_user_detail_for_buylead(buylead_response)` (~1088), add:
```python
                buyer_activity, buyer_glid = await _fetch_buyer_activity_for_bl(buylead_response, trace)
```

- [ ] **Step 8: Streaming handler — pass activity into the agent**

In the streaming `asyncio.gather` (~1104), change:
```python
                    _timed(run_buyer_profile_agent(offer_id, buylead_response, user_detail=user_detail, _trace=True)),
```
to:
```python
                    _timed(run_buyer_profile_agent(offer_id, buylead_response, buyer_activity=buyer_activity, _trace=True)),
```

- [ ] **Step 9: Streaming handler — replace the post-gather activity call**

Replace (~1364–1368):
```python
                # Buyer Activity + activity-keyword selection (non-fatal).
                _, _, batch_activity_kw, _ = await _resolve_buyer_activity(
                    buylead_response, payload, desc_result, trace,
                )
                batch_activity_keywords = (batch_activity_kw or {}).get("keywords") or []
```
with:
```python
                # desc-dependent activity-keyword enrichment (activity already fetched
                # pre-gather). Non-fatal.
                batch_activity_kw, _ = await _enrich_activity_keywords(
                    buyer_activity, buylead_response, payload, desc_result, trace,
                )
                batch_activity_keywords = (batch_activity_kw or {}).get("keywords") or []
```

- [ ] **Step 10: Streaming handler — update the buyer-profile failure dict**

Replace (~1346–1353):
```python
                    buyer_profile_result = {
                        "Display_id": offer_id,
                        "Genuineness": "Not Available", "Score": None, "Confidence": "None",
                        "Reason": "Buyer Profile Agent failed; see error.",
                        "Profile_Status": "Incomplete",
                        "Profile_Check_Reason": "Buyer Profile Agent failed; see error.",
                        "Tenure": "Unknown", "Glid": "", "error": str(buyer_profile_value),
                    }
```
with:
```python
                    buyer_profile_result = {
                        "Display_id": offer_id,
                        "status": "not related",
                        "reasoning": "Buyer Profile Agent failed; see error.",
                        "Glid": "", "error": str(buyer_profile_value),
                    }
```

- [ ] **Step 11: Verify the router imports cleanly + no stale symbols**

Run:
```bash
python -c "import app.routers.audit; print('ok')"
```
Expected: prints `ok`.

Run (Bash tool) — confirm the old helper is gone and no stale buyer_profile output keys remain in the router:
```bash
grep -n "_resolve_buyer_activity" app/routers/audit.py; echo "---"; grep -nE "buyer_profile_result[^;]*(Genuineness|Profile_Status|Tenure|Profile_Check_Reason)" app/routers/audit.py; echo "done"
```
Expected: first grep prints nothing (function removed and all call sites replaced); second grep prints nothing; then `done`.

- [ ] **Step 12: Commit**

```bash
git add app/routers/audit.py
git commit -m "refactor(audit): fetch buyer activity pre-gather and feed it to buyer_profile agent"
```

---

### Task 3: CSV schema swap + archive old log

**Files:**
- Modify: `app/services/audit_log_service.py`
- Archive: `audit_dashboard_log.csv` → `audit_dashboard_log.csv.bak`

**Interfaces:**
- Consumes: `buyer_profile_response` dict with keys `status`, `reasoning`, `error` (Task 1/2).
- Produces: CSV columns `buyer_profile_status`, `buyer_profile_reasoning`, `buyer_profile_error`.

- [ ] **Step 1: Swap the column definitions**

In `app/services/audit_log_service.py`, replace the 8 buyer_profile column entries (~96–105):
```python
    # Buyer Profile Agent (standalone — not part of BL Score 1/2). Genuineness is an
    ...
    "buyer_profile_genuineness",
    "buyer_profile_score",
    "buyer_profile_confidence",
    "buyer_profile_reason",
    "buyer_profile_status",
    "buyer_profile_check_reason",
    "buyer_profile_tenure",
    "buyer_profile_error",
```
with:
```python
    # Buyer Profile Agent (standalone — BL-title coherence auditor, not part of BL Score 1/2).
    "buyer_profile_status",      # "related" | "not related"
    "buyer_profile_reasoning",
    "buyer_profile_error",
```
(Preserve the exact comment lines already present above the first entry only insofar as they describe these columns; drop the stale genuineness comment.)

- [ ] **Step 2: Swap the row mapping**

Replace the buyer_profile mapping entries (~403–410):
```python
        "buyer_profile_genuineness": buyer_profile_response.get("Genuineness", ""),
        "buyer_profile_score": buyer_profile_response.get("Score", "") if buyer_profile_response.get("Score") is not None else "",
        "buyer_profile_confidence": buyer_profile_response.get("Confidence", ""),
        "buyer_profile_reason": buyer_profile_response.get("Reason", ""),
        "buyer_profile_status": buyer_profile_response.get("Profile_Status", ""),
        "buyer_profile_check_reason": buyer_profile_response.get("Profile_Check_Reason", ""),
        "buyer_profile_tenure": buyer_profile_response.get("Tenure", ""),
        "buyer_profile_error": buyer_profile_response.get("error", ""),
```
with:
```python
        "buyer_profile_status": buyer_profile_response.get("status", ""),
        "buyer_profile_reasoning": buyer_profile_response.get("reasoning", ""),
        "buyer_profile_error": buyer_profile_response.get("error", ""),
```

- [ ] **Step 3: Verify columns + row mapping stay in sync**

Run (Bash tool):
```bash
python - <<'PY'
import inspect, app.services.audit_log_service as m
cols = [c for c in dir(m)]
# Find the columns list (name may vary) and assert new/old membership via source scan.
src = inspect.getsource(m)
for stale in ("buyer_profile_genuineness", "buyer_profile_score", "buyer_profile_confidence",
              "buyer_profile_reason", "buyer_profile_check_reason", "buyer_profile_tenure"):
    assert stale not in src, f"stale column still present: {stale}"
for new in ("buyer_profile_status", "buyer_profile_reasoning", "buyer_profile_error"):
    assert new in src, f"missing new column: {new}"
print("csv schema checks passed")
PY
```
Expected: prints `csv schema checks passed`.

- [ ] **Step 4: Archive the existing CSV (tracked data file)**

The append writer only writes the header when the file is absent, so the old file must be moved aside for the new schema to take effect.

Run (Bash tool):
```bash
if [ -f audit_dashboard_log.csv ]; then git mv audit_dashboard_log.csv audit_dashboard_log.csv.bak && echo "archived"; else echo "no existing csv"; fi
```
Expected: `archived` (or `no existing csv`). If `DATA_DIR` is set to a non-repo path in the target environment, also move `"$DATA_DIR/audit_dashboard_log.csv"` aside there at deploy time (note in the commit message).

- [ ] **Step 5: Commit**

```bash
git add app/services/audit_log_service.py
git commit -m "feat(csv): replace buyer_profile columns with status/reasoning; archive old log"
```

---

### Task 4: UI — buyer_profile card

**Files:**
- Modify: `app/templates/result.html` (~537–550)

**Interfaces:**
- Consumes: `buyer_profile_result` with `status`, `reasoning`, `error`, `prev_buyleads`, `prev_enquiries`.

- [ ] **Step 1: Replace the buyer_profile stat block**

Replace lines ~537–550 (the `_bpg`/`_bps`/`_bpt` set + stat-card row + reason blocks), keeping the `_bpbl`/`_bpenq` sets and the "View prior activity" modal below untouched. Replace:
```jinja
      {% set _bpg = bp.get('Genuineness', '') %}
      {% set _bps = bp.get('Profile_Status', '') %}
      {% set _bpt = bp.get('Tenure', '') %}
      {% set _bpbl = bp.get('prev_buyleads') or [] %}
      {% set _bpenq = bp.get('prev_enquiries') or [] %}
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:0.75rem;">
        <div class="stat-card"><div class="stat-value" style="font-size:1rem;color:{{ 'var(--accent-green)' if _bpg == 'Correct' else ('#f87171' if _bpg == 'Incorrect' else 'var(--text-muted)') }};">{{ _bpg or '-' }}</div><div class="stat-label">Genuineness</div></div>
        <div class="stat-card"><div class="stat-value" style="font-size:1rem;">{{ bp.get('Score') if bp.get('Score') is not none else '-' }}</div><div class="stat-label">Score</div></div>
        <div class="stat-card"><div class="stat-value" style="font-size:1rem;">{{ bp.get('Confidence', '-') }}</div><div class="stat-label">Confidence</div></div>
        <div class="stat-card"><div class="stat-value" style="font-size:1rem;color:{{ 'var(--accent-green)' if _bps == 'Complete' else '#f87171' }};">{{ _bps or '-' }}</div><div class="stat-label">Profile</div></div>
        <div class="stat-card"><div class="stat-value" style="font-size:1rem;">{{ _bpt or '-' }}</div><div class="stat-label">Tenure</div></div>
      </div>
      <div class="check-reason">{{ bp.get('Reason', '-') or '-' }}</div>
      {% if bp.get('Profile_Check_Reason') %}<div class="check-reason" style="margin-top:6px;color:var(--text-muted);">Profile: {{ bp.get('Profile_Check_Reason') }}</div>{% endif %}
```
with:
```jinja
      {% set _bpstatus = bp.get('status', '') %}
      {% set _bpbl = bp.get('prev_buyleads') or [] %}
      {% set _bpenq = bp.get('prev_enquiries') or [] %}
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:0.75rem;">
        <div class="stat-card"><div class="stat-value" style="font-size:1rem;color:{{ 'var(--accent-green)' if _bpstatus == 'related' else ('#f87171' if _bpstatus == 'not related' else 'var(--text-muted)') }};">{{ _bpstatus or '-' }}</div><div class="stat-label">Coherence</div></div>
      </div>
      <div class="check-reason">{{ bp.get('reasoning', '-') or '-' }}</div>
```
(Leave the existing `{% if bp.get('error') %}...` line and the `{% if _bpbl or _bpenq %}` modal that follow unchanged.)

- [ ] **Step 2: Verify template renders + no stale fields in the bp block**

Run:
```bash
python -c "from jinja2 import Environment, FileSystemLoader; Environment(loader=FileSystemLoader('app/templates')).get_template('result.html'); print('template parses')"
```
Expected: prints `template parses` (Jinja syntax OK).

Run (Bash tool):
```bash
grep -nE "bp.get\('(Genuineness|Profile_Status|Tenure|Score|Confidence|Reason|Profile_Check_Reason)'\)" app/templates/result.html; echo done
```
Expected: prints only `done` (no stale buyer_profile fields in the `bp` block; note the `br`/buyer_viewed block legitimately still uses `Genuineness`, so confirm any hits belong to `br`, not `bp`).

- [ ] **Step 3: Commit**

```bash
git add app/templates/result.html
git commit -m "feat(ui): buyer_profile card shows coherence status + reasoning"
```

---

## Self-Review

**Spec coverage:**
- Agent core rewrite + guardrail + CSL trim → Task 1. ✓
- New prompt (static system prompt) → Task 1 Step 1. ✓
- Orchestration reorder (split helper, pre-gather fetch, pass-in) both handlers → Task 2. ✓
- CSV 8→3 columns + archive → Task 3. ✓
- UI card → Task 4. ✓
- Trace fallbacks (`_BUYER_PROFILE_FAIL` + inline dicts, single + streaming) → Task 2 Steps 5,6,10. ✓
- Removed User-Detail dependency in agent; ISQ fetch untouched → Task 1 (agent no longer imports/uses it); Task 2 keeps `user_detail` for ISQ. ✓

**Placeholder scan:** No TBD/TODO; all steps carry exact code. ✓

**Type consistency:** `run_buyer_profile_agent(offer_id, buylead_response, buyer_activity=None, _trace=False)` used identically in both handlers; result keys `status`/`reasoning`/`Glid`/`error` consistent across agent, CSV mapping, UI, and fallbacks; `_fetch_buyer_activity_for_bl`/`_enrich_activity_keywords` signatures match their call sites. ✓

**Note on `buyer_glid`:** now assigned from `_fetch_buyer_activity_for_bl` before the gather in both handlers; the later `_resolve_buyer_activity` no longer returns it — its previous single-handler consumers are unaffected (template uses `buyer_activity_result`, and the trace-reconstruction path derives glid independently).
