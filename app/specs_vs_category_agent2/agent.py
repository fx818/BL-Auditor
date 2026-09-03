"""Specs vs Category Agent 2 — async LangChain wrapper.

Reads the buylead's MCAT and ISQ specs and asks the LLM to classify whether
the specs are appropriate for the category.

LLM output contract (from prompt.md):
    {
        "status": "Correct" | "Incorrect" | "Not Available" | "Error",
        "score": float (0.0–1.0),
        "confidence": "High" | "Medium" | "Low",
        "reason": str,
        "issues": [{"type": str, "fields": [str], "detail": str}]
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

from app.services.buylead_service import parse_enrichmentinfo_to_isq

log = logging.getLogger("bl-auditor.specs_vs_category_agent2")
_LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))


class SpecsVsCategoryAgent2State(TypedDict, total=False):
    offer_id: str
    buylead_response: Dict[str, Any]
    agent_input: Dict[str, Any]
    system_prompt: str
    user_message: str
    raw_output: str
    result: Dict[str, Any]


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
    return get_active_prompt("specs_vs_category_agent2")[0]


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


async def _prepare_input(state: SpecsVsCategoryAgent2State) -> SpecsVsCategoryAgent2State:
    data = (state["buylead_response"] or {}).get("RESPONSE", {}).get("DATA", {}) or {}
    mcat_name = str(data.get("PRIME_MCAT_NAME") or "Unknown")
    offer_display_id = data.get("ETO_OFR_DISPLAY_ID") or state["offer_id"]
    isq_list = parse_enrichmentinfo_to_isq(data.get("ENRICHMENTINFO"))
    isq_table = _build_isq_table(isq_list)
    return {
        "agent_input": {
            "Display_id": offer_display_id,
            "mcat_name": mcat_name,
            "isq_table": isq_table,
            "isq_count": len(isq_list),
        }
    }


async def _classify(state: SpecsVsCategoryAgent2State) -> SpecsVsCategoryAgent2State:
    base_url = os.getenv("LLM_BASE_URL")
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    timeout = float(os.getenv("LLM_TIMEOUT", "60"))

    if not api_key or not model:
        raise RuntimeError("Missing LLM_API_KEY or LLM_MODEL")

    agent_input = state["agent_input"]

    if agent_input.get("isq_count", 0) == 0:
        result = {
            "Display_id": agent_input.get("Display_id", state.get("offer_id", "")),
            "Status": "Not Available",
            "Score": 0.0,
            "Confidence": "High",
            "Reason": "No ISQ specs were provided in the BuyLead.",
            "Issues": [{"type": "missing", "fields": ["ALL"], "detail": "ISQ list is empty."}],
        }
        return {
            "system_prompt": "(skipped — no ISQs)",
            "user_message": "(skipped — no ISQs)",
            "raw_output": json.dumps(result),
            "result": result,
        }

    system_prompt = _read_prompt()

    user_message = (
        f"Audit the following BuyLead's specs against its category.\n\n"
        f"Category (MCAT): {agent_input['mcat_name']}\n\n"
        f"ISQ Specs:\n{agent_input['isq_table']}\n\n"
        "Return ONLY the JSON object described in the system prompt. No prose, no markdown fences."
    )

    last_exc: Exception | None = None
    response = None
    for attempt in range(1, _LLM_MAX_RETRIES + 2):
        try:
            try:
                llm = ChatOpenAI(
                    model=model, api_key=api_key, base_url=base_url, timeout=timeout,
                    model_kwargs={"response_format": {"type": "json_object"}, "reasoning_effort": "minimal"},
                    extra_body={"google": {"thinking_config": {"thinking_budget": 0}}},
                )
                response = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_message)])
            except Exception:
                llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url, timeout=timeout)
                response = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_message)])
            break
        except Exception as exc:
            last_exc = exc
            log.warning("specs_vs_category_agent2 attempt %d/%d failed: %s", attempt, _LLM_MAX_RETRIES + 1, exc)
            if attempt <= _LLM_MAX_RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))
    else:
        raise RuntimeError(f"specs_vs_category_agent2 exhausted {_LLM_MAX_RETRIES + 1} attempts: {last_exc}") from last_exc

    raw_output = str(response.content)
    try:
        parsed = _clean_output(raw_output)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"specs_vs_category_agent2 parse failed: {exc}. Raw: {raw_output[:500]!r}") from exc

    parsed.setdefault("Display_id", agent_input.get("Display_id", ""))
    return {
        "system_prompt": system_prompt,
        "user_message": user_message,
        "raw_output": raw_output,
        "result": parsed,
    }


def _build_graph():
    graph = StateGraph(SpecsVsCategoryAgent2State)
    graph.add_node("prepare_input", _prepare_input)
    graph.add_node("classify", _classify)
    graph.set_entry_point("prepare_input")
    graph.add_edge("prepare_input", "classify")
    graph.add_edge("classify", END)
    return graph.compile()


@lru_cache(maxsize=1)
def _compiled_graph():
    return _build_graph()


async def run_specs_vs_category_agent2(
    offer_id: str,
    buylead_response: Dict[str, Any],
    _trace: bool = False,
) -> Dict[str, Any]:
    from app.services.langfuse_service import get_langfuse_handler
    _h = get_langfuse_handler()
    _cfg = {"callbacks": [_h], "run_name": f"specs_vs_category_agent2:{offer_id}"} if _h else {}
    state = await _compiled_graph().ainvoke(
        {"offer_id": offer_id, "buylead_response": buylead_response},
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
