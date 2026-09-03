"""Recheck Agent — second-opinion LLM call for flagged product-validation agents.

Runs only when exactly 1 or 2 of the 4 core product-validation agents return
"Incorrect". Uses the same system prompt as the Windmill recheck step.

Returns:
    {
        "ran": True,
        "results": [{"agent": str, "status": "not_outlier"|"outlier", "reason": str}],
        "raw_output": str,
    }
    On LLM/parse failure:
    {
        "ran": True,
        "error": str,
        "results": [],
    }
"""
import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

log = logging.getLogger("bl-auditor.recheck_agent")
_LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))

# Internal agent key → Windmill agent name (used in prompt and output)
AGENT_NAME_MAP: Dict[str, str] = {
    "title_vs_category": "Title vs MCAT Agent",
    "title_vs_specs":    "Title ISQ Agent",
    "specs_vs_category": "ISQ vs MCAT Agent",
    "isq_validation":    "Inter ISQ Agent",
}

_SYSTEM_PROMPT = """\
# Check Agent — IndiaMART BuyLead Auditor

`Agent` is a SELECTION list telling you which checks to run. Run ONLY the agents named in `Agent`, in that exact order. Do not run, mention, or output any agent absent from `Agent`.

Input:
{"Title": "...", "MCAT": "...", "ISQs": {...}, "Agent": [...]}

Output array length MUST equal input `Agent` length, same order, no additions/omissions.

Global rules:
* Ignore Probable Order Value / Probable Requirement Type for outlier calls — context only.
* Buyer Filled Details / Additional Requirements = supplementary, not strict constraints.
* Never infer missing info or buyer intent. Insufficient evidence → not_outlier.
* outlier requires a clear, direct contradiction. Default to not_outlier when ambiguous.
* Judge each agent independently, no carry-over.

## Definitions (apply ONLY those named in `Agent`)

**Title vs MCAT Agent** — Identify the Title's core entity (product/instrument/service/troupe/agency, etc.) AND its target segment (baby/infant, kids, girls, boys, men, women, unisex). Compare both against MCAT.

outlier if EITHER:
1. **Entity-type mismatch** — Title's core entity is a different KIND of thing than MCAT, even if a keyword or generic word (Agency, Band, Company) is shared.
   - Title "Puneri Dhol Tasha Pathak Musical Band", MCAT "Dhol" → outlier (band/service vs. instrument).
   - Title "Ola Party Diamond Agency", MCAT "Modeling Agencies" → outlier.
2. **Segment mismatch** — Title and MCAT name different target age/gender segments for the same product, even if the base product word matches. Baby/infant, kids, girls, boys, men, women, unisex are distinct unless identical or direct synonyms — do not treat one as a subset of another by default.
   - Title "Baby Dresses", MCAT "Girls Casual Dresses" → outlier (baby ≠ girls segment).

not_outlier — Title is the same entity type AND same segment as MCAT, or a narrower style/subtype within both (e.g. Title "PVC Pipe", MCAT "PVC Pipes"; Title "Girls Party Dresses", MCAT "Girls Casual Dresses" — same segment, style variant).

Ignore: quantity, size, brand, colour, packaging, order value. Shared keywords alone never establish coherence — always check entity type + segment.

**Title ISQ Agent** — Compare Title vs ISQs (material, size, capacity, grade). outlier only if an ISQ directly contradicts Title's core identity/spec (e.g. Title "Steel Tank", ISQ Material: Plastic). Ignore Quantity/Order Value/Requirement Type. Never flag an ISQ that merely adds detail.

**ISQ vs MCAT Agent** — Compare ISQs vs MCAT category. outlier only if ISQs as a whole describe a different, unrelated category than MCAT. Ignore Order Value/Requirement Type. Never flag a narrower variant/spec within MCAT.

**Inter ISQ Agent** — Compare ISQs against each other. Check BOTH sub-rules below; outlier if either fires.

1. **Variant contradiction** — Two ISQs are mutually exclusive, OR Quantity implies a single unit/piece while other ISQs assign it contradictory variant values (e.g. Qty: 1 pc, Material: Steel AND Plastic). Never flag legitimate co-existing variants or Quantity implying multiple units.

2. **Package-quantity math** — A per-package/per-box/per-carton/per-unit quantity ISQ (e.g. "Quantity per Package", "Pieces per Box") vs a total/order quantity ISQ (e.g. "Total Quantity", "Order Quantity"). Apply this exact procedure, in order:
   a. Both fields must be present with explicit numeric values. If either is missing, blank, or non-numeric → skip this check, not_outlier.
   b. Compare units exactly as stated. Only proceed if the two ISQs use the same unit, or the ISQ set itself states an explicit conversion (e.g. "1 box = 10 pcs" given verbatim in the ISQs). If units differ and no explicit conversion is stated in the input → do NOT convert, do NOT assume, skip this check, not_outlier.
   c. Never invent, assume, or look up a conversion factor (e.g. do not assume 1 box = 12 pcs, 1 kg = X pcs, etc.) — only use a conversion if it is stated verbatim in the ISQs.
   d. With directly comparable numbers: outlier if per-package/per-unit quantity > total/order quantity. not_outlier if per-package/per-unit quantity ≤ total/order quantity.
   - Example outlier: ISQ "Quantity per Package: 150 pcs", "Total Quantity: 100 pcs" → outlier (150 > 100, same unit).
   - Example not_outlier: ISQ "Quantity per Package: 10 pcs", "Total Quantity: 100 pcs" → not_outlier (10 ≤ 100).
   - Example not_outlier (no conversion given): ISQ "Quantity per Package: 5 kg", "Total Quantity: 100 pcs" → not_outlier (units differ, no stated conversion — do not guess).

Ignore Order Value/Requirement Type throughout.

Reasoning: one factual sentence per agent, citing only the compared fields (for Inter ISQ package-math outlier calls, state the two numeric values and units compared). No "probably," "seems," or references to other agents.

Output — JSON array only, no markdown/extra text. Exactly these keys in order: `Agent`, `status`, `reason`. `status` is exactly "outlier" or "not_outlier".

Example (Agent = ["Title vs MCAT Agent", "Inter ISQ Agent"]):
[
  {"Agent": "Title vs MCAT Agent", "status": "not_outlier", "reason": "..."},
  {"Agent": "Inter ISQ Agent", "status": "outlier", "reason": "..."}
]

Verify before finalizing: output length == input `Agent` length, order matches.\
"""


def _clean_output(raw: str) -> List[Dict[str, Any]]:
    text = (raw or "").replace("```json", "").replace("```", "").strip()
    if not text:
        raise ValueError("LLM returned empty response")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            raise ValueError(f"No JSON array in LLM output: {text[:300]}")
        parsed = json.loads(match.group(0))
    # json_object mode wraps the array in a dict — unwrap it
    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                parsed = v
                break
        else:
            raise ValueError(f"Expected JSON array, got dict with no list value")
    if not isinstance(parsed, list):
        raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")
    return parsed


async def run_recheck_agent(
    offer_id: str,
    buylead_response: Dict[str, Any],
    flagged_keys: List[str],
) -> Dict[str, Any]:
    """Re-check flagged agents with a single LLM call.

    flagged_keys: list of internal keys from AGENT_NAME_MAP that returned Incorrect.
    """
    data = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {}) or {}
    title = str(data.get("ETO_OFR_TITLE") or "")
    mcat = str(data.get("PRIME_MCAT_NAME") or "")
    isqs = data.get("ENRICHMENTINFO") or {}

    agent_names = [AGENT_NAME_MAP[k] for k in flagged_keys if k in AGENT_NAME_MAP]
    if not agent_names:
        return {"ran": True, "results": [], "raw_output": ""}

    user_message = (
        f"Agent: {json.dumps(agent_names)}, "
        f"Title: {title}, "
        f"MCAT: {mcat}, "
        f"ISQs: {json.dumps(isqs, ensure_ascii=False)}"
    )

    base_url = os.getenv("LLM_BASE_URL")
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    timeout = float(os.getenv("LLM_TIMEOUT", "60"))

    if not api_key or not model:
        return {"ran": True, "error": "Missing LLM_API_KEY or LLM_MODEL", "results": []}

    last_exc: Exception | None = None
    response = None
    for attempt in range(1, _LLM_MAX_RETRIES + 2):
        try:
            try:
                llm = ChatOpenAI(
                    model=model, api_key=api_key, base_url=base_url, timeout=timeout,
                    model_kwargs={"reasoning_effort": "minimal"},
                    extra_body={"google": {"thinking_config": {"thinking_budget": 0}}},
                )
                response = await llm.ainvoke([SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=user_message)])
            except Exception:
                llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url, timeout=timeout)
                response = await llm.ainvoke([SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=user_message)])
            break
        except Exception as exc:
            last_exc = exc
            log.warning("recheck_agent attempt %d/%d failed: %s", attempt, _LLM_MAX_RETRIES + 1, exc)
            if attempt <= _LLM_MAX_RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))
    else:
        err = f"recheck_agent exhausted {_LLM_MAX_RETRIES + 1} attempts: {last_exc}"
        log.error(err)
        return {"ran": True, "error": err, "results": []}

    raw_output = str(response.content)
    try:
        items = _clean_output(raw_output)
    except (ValueError, json.JSONDecodeError) as exc:
        err = f"recheck_agent parse failed: {exc}. Raw: {raw_output[:500]!r}"
        log.error(err)
        return {"ran": True, "error": err, "results": [], "raw_output": raw_output}

    # Normalize: accept both "Agent" and "agent" key names
    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        agent_name = item.get("Agent") or item.get("agent") or ""
        status = str(item.get("status") or "").strip().lower()
        reason = str(item.get("reason") or "")
        if agent_name and status in ("not_outlier", "outlier"):
            results.append({"agent": agent_name, "status": status, "reason": reason})

    return {"ran": True, "results": results, "raw_output": raw_output}
