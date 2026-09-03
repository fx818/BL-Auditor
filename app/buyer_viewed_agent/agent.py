"""Buyer Viewed Agent — async LangChain wrapper.

Reads the buylead's MCAT, title, and the buyer's previously enquired products
and asks the LLM to decide whether those enquiries are related to the current
BuyLead (outlier vs not).

LLM output contract (from prompt.md) — validated by ``BuyerViewedOutput``:
    {
        "status": "outlier" | "not_outlier",
        "reason": str,
        "product_matched": str   # e.g. "2/3" (related_count/total)
    }
"""
import asyncio
import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ValidationError

log = logging.getLogger("bl-auditor.buyer_viewed_agent")
_LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))


PROMPT_PATH = Path(__file__).resolve().with_name("prompt.md")


class BuyerViewedOutput(BaseModel):
    """Strict schema for the buyer-viewed LLM output.

    Missing fields fall back to the guardrail default (a benign ``not_outlier``);
    a present-but-invalid ``status`` raises ``ValidationError`` so the pipeline
    surfaces the LLM regression instead of silently defaulting.
    """

    status: Literal["outlier", "not_outlier"] = "not_outlier"
    reason: str = (
        "No prior product enquiries available — cannot assess relatedness to the current BuyLead."
    )
    product_matched: str = "0/0"


class BuyerViewedState(TypedDict, total=False):
    offer_id: str
    buylead_response: Dict[str, Any]
    agent_input: Dict[str, Any]
    system_prompt: str
    user_message: str
    raw_output: str
    result: Dict[str, Any]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return "" if text in {"null", "undefined"} else text


def _parse_jsonish(raw: Any) -> Any:
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raw = json.dumps(raw)
    raw = raw.replace('\\"', '"')
    parsed = json.loads(raw)
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    return parsed


def _read_prompt() -> str:
    from app.services.prompt_override_service import get_active_prompt
    return get_active_prompt("buyer_viewed")[0]


def _render_template(template: str, data: Dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        value = data.get(key, "")
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    return re.sub(r"{{\s*([^}]+)\s*}}", replace, template)


def _extract_products_enquired(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    products: List[Dict[str, Any]] = []
    for product in data.get("PRODUCTS_ENQUIRED") or []:
        if not isinstance(product, dict):
            continue
        item_name = str(product.get("FK_PC_ITEM_NAME") or product.get("FK_PC_ITEM_DISPLAY_NAME") or "").strip()
        if not item_name:
            continue
        price = str(product.get("PRODUCT_PRICE") or "").replace("\\u20b9", "₹").strip()
        img = str(product.get("PC_IMG_SMALL_100X100") or "").strip()
        products.append({"FK_PC_ITEM_NAME": item_name, "PRODUCT_PRICE": price, "PC_IMG_SMALL_100X100": img})
    return products


def _build_offer_source(offer_id: str, buylead_response: Dict[str, Any]) -> Dict[str, Any]:
    data = buylead_response.get("RESPONSE", {}).get("DATA", {})
    products = _extract_products_enquired(data)[:3]
    return {
        "Display_id": data.get("ETO_OFR_DISPLAY_ID") or offer_id,
        "Title": data.get("ETO_OFR_TITLE") or "",
        "MCAT": data.get("PRIME_MCAT_NAME") or data.get("ETO_OFR_GLCAT_MCAT_NAME") or "",
        "MCAT_id": data.get("FK_GLCAT_MCAT_ID") or "",
        "PRODUCTS_ENQUIRED": products,
    }


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


async def _prepare_input(state: BuyerViewedState) -> BuyerViewedState:
    offer_id = state["offer_id"]
    source = _build_offer_source(offer_id, state["buylead_response"])
    products = source["PRODUCTS_ENQUIRED"]
    agent_input: Dict[str, Any] = {
        "Display_id": source["Display_id"],
        "Title": source["Title"],
        "MCAT": source["MCAT"],
        "MCAT_id": source["MCAT_id"],
        "PRODUCTS_ENQUIRED": products,
        "Products_Enquired_Count": len(products),
    }
    return {"agent_input": agent_input}


async def _buyer_classify(state: BuyerViewedState) -> BuyerViewedState:
    base_url = os.getenv("LLM_BASE_URL")
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    timeout = float(os.getenv("LLM_TIMEOUT", "60"))

    if not api_key or not model:
        raise RuntimeError("Missing LLM_API_KEY or LLM_MODEL")

    agent_input = state["agent_input"]
    raw_prompt = _read_prompt()
    system_prompt = _render_template(raw_prompt, agent_input)

    user_text = "\n".join([
        f"Display_id: {agent_input.get('Display_id', '')}",
        f"Title: {agent_input.get('Title', '')}",
        f"MCAT: {agent_input.get('MCAT', '')}",
        f"MCAT_id: {agent_input.get('MCAT_id', '')}",
        f"PRODUCTS_ENQUIRED: {json.dumps(agent_input.get('PRODUCTS_ENQUIRED', []), ensure_ascii=False)}",
        f"Products_Enquired_Count: {agent_input.get('Products_Enquired_Count', 0)}",
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
                "buyer_viewed LLM attempt %d/%d failed: %s: %s",
                attempt, _LLM_MAX_RETRIES + 1, type(exc).__name__, exc,
            )
            if attempt <= _LLM_MAX_RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))
    else:
        raise RuntimeError(
            f"Buyer viewed LLM exhausted {_LLM_MAX_RETRIES + 1} attempts: {last_exc}"
        ) from last_exc

    raw_output = str(response.content)
    try:
        parsed = _clean_classifier_output(raw_output)
    except ValueError as exc:
        raise RuntimeError(f"Buyer viewed LLM parse failed: {exc}") from exc

    try:
        result = BuyerViewedOutput.model_validate(parsed).model_dump()
    except ValidationError as exc:
        raise RuntimeError(f"Buyer viewed output failed schema validation: {exc}") from exc

    return {
        "system_prompt": system_prompt,
        "user_message": user_text,
        "raw_output": raw_output,
        "result": result,
    }


def _build_graph():
    graph = StateGraph(BuyerViewedState)
    graph.add_node("prepare_input", _prepare_input)
    graph.add_node("buyer_classify", _buyer_classify)
    graph.set_entry_point("prepare_input")
    graph.add_edge("prepare_input", "buyer_classify")
    graph.add_edge("buyer_classify", END)
    return graph.compile()


@lru_cache(maxsize=1)
def _compiled_graph():
    return _build_graph()


async def run_buyer_viewed_agent(
    offer_id: str,
    buylead_response: Dict[str, Any],
    _trace: bool = False,
) -> Dict[str, Any]:
    from app.services.langfuse_service import get_langfuse_handler
    _h = get_langfuse_handler()
    _cfg = {"callbacks": [_h], "run_name": f"buyer_viewed_agent:{offer_id}"} if _h else {}
    state = await _compiled_graph().ainvoke(
        {
            "offer_id": offer_id,
            "buylead_response": buylead_response,
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
