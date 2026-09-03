"""ISQ Validation Agent — async LangGraph agent.

Reads the buylead's ISQ list and asks the LLM to classify internal coherence.

LLM output contract (from prompt.md):
    {
        "status": "outlier" | "not_outlier",
        "reason": str  (1-3 sentences citing Spec names verbatim)
    }
"""
import asyncio
import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, field_validator

from app.services.buylead_service import parse_enrichmentinfo_to_isq

log = logging.getLogger("bl-auditor.isq_validation_agent")
_LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))


class ISQResult(BaseModel):
    status: str
    reason: str

    @field_validator("status")
    @classmethod
    def _validate_status(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("outlier", "not_outlier"):
            return "not_outlier"
        return v

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, v: str) -> str:
        return str(v).strip()


class ISQValidationState(TypedDict, total=False):
    offer_id: str
    buylead_response: Dict[str, Any]
    user_detail: Dict[str, Any]
    agent_input: Dict[str, Any]
    system_prompt: str
    user_message: str
    raw_output: str
    result: Dict[str, Any]


# Total-order quantity ISQ spec names (mirrors the "Total order quantity" role in
# prompt.md). Only these rows are checked against the buyer's own mobile/zip.
_QTY_SPEC_NAMES = {"quantity", "order qty", "required quantity", "qty"}


def _digits_only(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _quantity_identity_hit(
    isq_list: List[Dict[str, str]], mobile: str, zip_code: str
):
    """Deterministic hard check: does a quantity-role ISQ value's digits EXACTLY
    equal the buyer's own mobile number or PIN code?

    Returns ``(spec_name, raw_value, match_kind, matched_value)`` on a hit, else
    ``None``. Catches buyers who paste their phone/pincode into the quantity field.
    """
    mob = _digits_only(mobile)
    zp = _digits_only(zip_code)
    if not mob and not zp:
        return None
    for item in isq_list or []:
        spec = str(item.get("IM_SPEC_MASTER_DESC", "")).strip()
        if spec.lower() not in _QTY_SPEC_NAMES:
            continue
        raw_value = str(item.get("ISQ_RESPONSE", "")).strip()
        qty_digits = _digits_only(raw_value)
        if not qty_digits:
            continue
        if mob and qty_digits == mob:
            return (spec, raw_value, "mobile number", mobile)
        if zp and qty_digits == zp:
            return (spec, raw_value, "PIN code", zip_code)
    return None


def _apply_quantity_identity_check(
    result: Dict[str, Any],
    isq_list: List[Dict[str, str]],
    user_detail: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge the deterministic mobile/zip hard check into the LLM result.

    - not_outlier + hard clean -> unchanged
    - not_outlier + hard hit   -> outlier, hard-check reason
    - outlier + hard clean     -> unchanged (LLM reason)
    - outlier + hard hit       -> outlier, LLM reason + hard-check reason
    """
    mobile = str((user_detail or {}).get("glusr_usr_ph_mobile") or "")
    zip_code = str((user_detail or {}).get("glusr_usr_zip") or "")
    hit = _quantity_identity_hit(isq_list, mobile, zip_code)
    if not hit:
        return result

    spec, raw_value, kind, matched = hit
    hard_reason = (
        f"Hard check: quantity '{raw_value}' (spec '{spec}') exactly matches the "
        f"buyer's own {kind} ({matched}) — the quantity field is misused, not a real order quantity."
    )

    was_outlier = result.get("status") == "outlier"
    result["status"] = "outlier"
    if was_outlier:
        prev = str(result.get("reason") or "").strip()
        result["reason"] = f"{prev} {hard_reason}".strip() if prev else hard_reason
    else:
        result["reason"] = hard_reason
    return result


def _apply_quantity_audit(
    result: Dict[str, Any],
    offer_id: str,
    buylead_response: Dict[str, Any],
    user_detail: Dict[str, Any],
) -> Dict[str, Any]:
    """Run the full quantity audit (R1–R6) and merge any Absurd finding into the ISQ result.

    Supersedes the narrow _apply_quantity_identity_check (R6 only). Non-fatal: any
    parse/import error is swallowed so a broken quantity agent never kills ISQ.
    """
    try:
        from app.quantity_agent.agent import run_quantity_agent
        qty_audit = run_quantity_agent(offer_id, buylead_response, user_detail=user_detail or {})
    except Exception:
        return result

    if qty_audit.get("status") != "Absurd":
        return result

    rules_fired = qty_audit.get("rules_fired") or []
    raw_reason = qty_audit.get("reason") or "quantity flagged as absurd"
    combined_reason = f"Quantity audit ({', '.join(rules_fired)}): {raw_reason}."

    was_outlier = result.get("status") == "outlier"
    result["status"] = "outlier"
    if was_outlier:
        prev = str(result.get("reason") or "").strip()
        result["reason"] = f"{prev} {combined_reason}".strip() if prev else combined_reason
    else:
        result["reason"] = combined_reason
    return result


def _build_isq_table(isq_list: List[Dict[str, str]]) -> str:
    if not isq_list:
        return "(none)"
    rows = ["| Spec | Value |", "| --- | --- |"]
    for item in isq_list:
        spec = str(item.get("IM_SPEC_MASTER_DESC", "")).strip() or "(unnamed)"
        value = str(item.get("ISQ_RESPONSE", "")).strip() or "(empty)"
        rows.append(f"| {spec.replace('|', '/')} | {value.replace('|', '/')} |")
    return "\n".join(rows)


def _read_prompt() -> str:
    from app.services.prompt_override_service import get_active_prompt
    return get_active_prompt("isq_validation")[0]


def _clean_output(raw: str) -> Dict[str, Any]:
    text = (raw or "").replace("```json", "").replace("```", "").strip()
    if not text:
        raise ValueError("LLM returned empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise ValueError(f"No JSON in LLM output: {text[:300]}")
        return json.loads(match.group(0))



def _build_user_message(agent_input: Dict[str, Any]) -> str:
    return (
        "Audit the following BuyLead's ISQ (In-Specification Questions) for internal coherence.\n\n"
        f"MCAT: {agent_input.get('mcat_name', '')}\n"
        f"Item: {agent_input.get('item_name', '')}\n\n"
        f"ISQs:\n{agent_input.get('isq_table', '(none)')}\n\n"
        "Return ONLY the JSON object described in the system prompt. No prose, no markdown fences."
    )


async def _prepare_input(state: ISQValidationState) -> ISQValidationState:
    data = (state["buylead_response"] or {}).get("RESPONSE", {}).get("DATA", {}) or {}
    mcat_name = str(data.get("PRIME_MCAT_NAME") or "Unknown")
    item_name = str(data.get("ETO_OFR_TITLE") or "")
    offer_display_id = data.get("ETO_OFR_DISPLAY_ID") or state["offer_id"]
    isq_list = parse_enrichmentinfo_to_isq(data.get("ENRICHMENTINFO"))
    isq_table = _build_isq_table(isq_list)
    return {
        "agent_input": {
            "Display_id": offer_display_id,
            "mcat_name": mcat_name,
            "item_name": item_name,
            "isq_list": isq_list,
            "isq_table": isq_table,
            "isq_count": len(isq_list),
        }
    }


async def _classify(state: ISQValidationState) -> ISQValidationState:
    base_url = os.getenv("LLM_BASE_URL")
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    timeout = float(os.getenv("LLM_TIMEOUT", "60"))

    if not api_key or not model:
        raise RuntimeError("Missing LLM_API_KEY or LLM_MODEL")

    agent_input = state["agent_input"]

    if agent_input.get("isq_count", 0) == 0:
        result = {
            "status": "not_outlier",
            "reason": "No ISQs were provided in the BuyLead.",
        }
        return {
            "system_prompt": "(skipped — no ISQs)",
            "user_message": "(skipped — no ISQs)",
            "raw_output": "",
            "result": result,
        }

    system_prompt = _read_prompt()
    user_message = _build_user_message(agent_input)

    last_exc: Exception | None = None
    response = None
    for attempt in range(1, _LLM_MAX_RETRIES + 2):
        try:
            try:
                llm = ChatOpenAI(
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                    timeout=timeout,
                    model_kwargs={
                        "response_format": {"type": "json_object"},
                        "reasoning_effort": "minimal",
                    },
                    extra_body={
                        "google": {"thinking_config": {"thinking_budget": 0}},
                    },
                )
                response = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_message)])
            except Exception:
                llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url, timeout=timeout)
                response = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_message)])
            break
        except Exception as exc:
            last_exc = exc
            log.warning(
                "isq_validation_agent attempt %d/%d failed: %s: %s",
                attempt, _LLM_MAX_RETRIES + 1, type(exc).__name__, exc,
            )
            if attempt <= _LLM_MAX_RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))
    else:
        raise RuntimeError(
            f"isq_validation_agent exhausted {_LLM_MAX_RETRIES + 1} attempts: {last_exc}"
        ) from last_exc

    raw_output = str(response.content)
    try:
        parsed = _clean_output(raw_output)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"isq_validation_agent parse failed: {exc}. Raw: {raw_output[:500]!r}"
        ) from exc

    # Validate via Pydantic — coerces status to outlier|not_outlier.
    parsed_model = ISQResult(
        status=str(parsed.get("status", "not_outlier")),
        reason=str(parsed.get("reason", parsed.get("reasoning", ""))),
    )
    result: Dict[str, Any] = parsed_model.model_dump()

    # Deterministic quantity audit: any Absurd ruling (R1–R6) forces outlier.
    result = _apply_quantity_audit(
        result, state["offer_id"], state["buylead_response"], state.get("user_detail") or {}
    )

    return {
        "system_prompt": system_prompt,
        "user_message": user_message,
        "raw_output": raw_output,
        "result": result,
    }


def _build_graph():
    graph = StateGraph(ISQValidationState)
    graph.add_node("prepare_input", _prepare_input)
    graph.add_node("classify", _classify)
    graph.set_entry_point("prepare_input")
    graph.add_edge("prepare_input", "classify")
    graph.add_edge("classify", END)
    return graph.compile()


@lru_cache(maxsize=1)
def _compiled_graph():
    return _build_graph()


async def run_isq_validation_agent(
    offer_id: str,
    buylead_response: Dict[str, Any],
    user_detail: Dict[str, Any] | None = None,
    _trace: bool = False,
) -> Dict[str, Any]:
    from app.services.langfuse_service import get_langfuse_handler
    _h = get_langfuse_handler()
    _cfg = {"callbacks": [_h], "run_name": f"isq_validation_agent:{offer_id}"} if _h else {}
    state = await _compiled_graph().ainvoke(
        {
            "offer_id": offer_id,
            "buylead_response": buylead_response,
            "user_detail": user_detail or {},
        },
        config=_cfg,
    )
    if _trace:
        return {
            "result": state["result"],
            "agent_input": state.get("agent_input", {}),
            "raw_output": state.get("raw_output", ""),
            "system_prompt": state.get("system_prompt", ""),
            "user_message": state.get("user_message", ""),
        }
    return state["result"]
