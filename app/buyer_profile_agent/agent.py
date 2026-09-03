"""Buyer Profile Agent — BL-title coherence auditor (standalone).

Given the current BuyLead and the buyer's recent evidence — prev BuyLeads (≤10),
prev enquiries (≤10), and the buyer activity log (CSL) — an LLM decides whether the
current BL title is coherent with that history: ``related`` or ``not related``.

Buyer activity is fetched once by the orchestration and passed in via ``buyer_activity``
(the parsed dict from ``buyer_activity_service.parse_buyer_activity``); the agent does
not fetch it. Any source failure degrades gracefully — the agent never raises.

The agent also emits two deterministic (no-LLM) signals from the User Detail API,
independent of the coherence verdict: profile completeness and buyer tenure.

Result dict:
    {
        "Display_id": str,
        "status": "related" | "not related",   # LLM coherence verdict
        "reasoning": str,
        "Profile_Status": "Complete" | "Incomplete",   # deterministic
        "Profile_Check_Reason": str,
        "Tenure": "New" | "Old" | "Unknown",           # deterministic
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
    USERS_API_URL,
    evaluate_profile_completeness,
    evaluate_tenure,
    extract_prev_bls,
    extract_prev_enqs,
    extract_user_profile,
    fetch_prev_buyleads,
    fetch_prev_enquiries,
    fetch_user_detail,
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

    from app.services.langfuse_service import get_langfuse_handler
    _h = get_langfuse_handler()
    llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url, timeout=timeout,
                     callbacks=[_h] if _h else None)

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


async def _passthrough(value: Any) -> Any:
    """Wrap an already-available value as an awaitable so it can flow through
    ``_timed_fetch`` alongside real fetches without a second API call."""
    return value


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
    user_detail: Dict[str, Any] | None = None,
    _trace: bool = False,
) -> Dict[str, Any]:
    data = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {}) or {}
    display_id = str(data.get("ETO_OFR_DISPLAY_ID") or offer_id)

    result: Dict[str, Any] = {
        "Display_id": display_id,
        "status": "not related",
        "reasoning": "",
        "Profile_Status": "Incomplete",
        "Profile_Check_Reason": "",
        "Tenure": "Unknown",
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
        result["Profile_Check_Reason"] = "No buyer GLID on BuyLead — profile checks unavailable."
        result["error"] = "missing FK_GLUSR_USR_ID"
        return _finalize(result, agent_input, raw_output, system_prompt, user_message, _trace)

    try:
        glid = decrypt_glid(str(enc_glid))
    except Exception as exc:
        result["reasoning"] = "GLID decryption failed; cannot assess relatedness."
        result["Profile_Check_Reason"] = "GLID decryption failed — profile checks unavailable."
        result["error"] = f"glid decrypt failed: {exc}"
        return _finalize(result, agent_input, raw_output, system_prompt, user_message, _trace)

    result["Glid"] = glid

    # User Detail may be supplied by the orchestration (fetched once, shared with
    # the ISQ agent) — reuse it instead of making a duplicate API call.
    user_source = _passthrough(user_detail) if user_detail is not None else fetch_user_detail(glid)
    prev_bl_rec, prev_enq_rec, user_rec = await asyncio.gather(
        _timed_fetch(fetch_prev_buyleads(glid), name="Prev BuyLeads API",
                     endpoint=LEADS_API_URL, input_={"glusrid": glid, "type": "B", "latest_lead": 10}),
        _timed_fetch(fetch_prev_enquiries(glid), name="Prev Enquiries API",
                     endpoint=LEADS_API_URL, input_={"glusrid": glid, "type": "E", "latest_lead": 10}),
        _timed_fetch(user_source, name="User Detail API",
                     endpoint=USERS_API_URL, input_={"glusrid": glid, "others": "ALL"}),
    )
    api_calls = [prev_bl_rec, prev_enq_rec, user_rec]

    # --- Deterministic (no LLM): profile completeness + tenure ---
    profile = extract_user_profile(user_rec["output"] or {})
    completeness = evaluate_profile_completeness(profile)
    result["Profile_Status"] = completeness["status"]
    result["Profile_Check_Reason"] = completeness["reason"]
    result["Tenure"] = evaluate_tenure(profile.get("membersince"))

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
        "profile": profile,
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
