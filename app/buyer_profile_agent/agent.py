import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph


PROMPT_PATH = Path(__file__).resolve().with_name("prompt.md")


class BuyerProfileState(TypedDict, total=False):
    offer_id: str
    buylead_response: Dict[str, Any]
    agent_input: Dict[str, Any]
    system_prompt: str
    user_message: str
    raw_output: str
    result: Dict[str, Any]
    prepare_sub_steps: List[Dict[str, Any]]
    classify_sub_steps: List[Dict[str, Any]]


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
    return PROMPT_PATH.read_text(encoding="utf-8")


def _render_template(template: str, data: Dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        value = data.get(key, "")
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    return re.sub(r"{{\s*([^}]+)\s*}}", replace, template)


def _extract_products_enquired(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Trim PRODUCTS_ENQUIRED entries to (item_name, price) only — drops image URLs."""
    products: List[Dict[str, Any]] = []
    for product in data.get("PRODUCTS_ENQUIRED") or []:
        if not isinstance(product, dict):
            continue
        item_name = str(product.get("FK_PC_ITEM_NAME") or product.get("FK_PC_ITEM_DISPLAY_NAME") or "").strip()
        if not item_name:
            continue
        price = str(product.get("PRODUCT_PRICE") or "").replace("\\u20b9", "₹").strip()
        products.append({"FK_PC_ITEM_NAME": item_name, "PRODUCT_PRICE": price})
    return products


def _build_offer_source(offer_id: str, buylead_response: Dict[str, Any]) -> Dict[str, Any]:
    data = buylead_response.get("RESPONSE", {}).get("DATA", {})
    products = _extract_products_enquired(data)
    return {
        "Display_id": data.get("ETO_OFR_DISPLAY_ID") or offer_id,
        "Title": data.get("ETO_OFR_TITLE") or "",
        "MCAT": data.get("PRIME_MCAT_NAME") or data.get("ETO_OFR_GLCAT_MCAT_NAME") or "",
        "MCAT_id": data.get("FK_GLCAT_MCAT_ID") or "",
        "PRODUCTS_ENQUIRED": products,
    }


def _clean_classifier_output(raw: str) -> Dict[str, Any]:
    raw = (raw or "").replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


async def _prepare_input(state: BuyerProfileState) -> BuyerProfileState:
    sub: List[Dict[str, Any]] = []
    offer_id = state["offer_id"]

    source = _build_offer_source(offer_id, state["buylead_response"])
    sub.append({"seq": 1, "node": "prepare_input", "fn": "_build_offer_source",
                "input": {"offer_id": offer_id}, "output": source})

    products = source["PRODUCTS_ENQUIRED"]
    agent_input: Dict[str, Any] = {
        "Display_id": source["Display_id"],
        "Title": source["Title"],
        "MCAT": source["MCAT"],
        "MCAT_id": source["MCAT_id"],
        "PRODUCTS_ENQUIRED": products,
        "Products_Enquired_Count": len(products),
    }
    sub.append({"seq": 2, "node": "prepare_input", "fn": "_merge_inputs[result]",
                "input": {"offer_keys": list(source.keys())}, "output": agent_input})

    return {"agent_input": agent_input, "prepare_sub_steps": sub}


async def _buyer_classify(state: BuyerProfileState) -> BuyerProfileState:
    base_url = os.getenv("BUYER_PROFILE_LLM_BASE_URL")
    api_key = os.getenv("BUYER_PROFILE_LLM_API_KEY")
    model = os.getenv("BUYER_PROFILE_LLM_MODEL")
    timeout = float(os.getenv("BUYER_PROFILE_LLM_TIMEOUT", "60"))

    if not api_key or not model:
        raise RuntimeError("Missing BUYER_PROFILE_LLM_API_KEY or BUYER_PROFILE_LLM_MODEL")

    sub: List[Dict[str, Any]] = []
    agent_input = state["agent_input"]

    raw_prompt = _read_prompt()
    sub.append({"seq": 1, "node": "buyer_classify", "fn": "_read_prompt",
                "input": {"path": str(PROMPT_PATH)},
                "output": {"char_count": len(raw_prompt)}})

    system_prompt = _render_template(raw_prompt, agent_input)
    sub.append({"seq": 2, "node": "buyer_classify", "fn": "_render_template",
                "input": {"variables": list(agent_input.keys())},
                "output": {"char_count": len(system_prompt)}})

    user_text = "\n".join([
        f"Display_id: {agent_input.get('Display_id', '')}",
        f"Title: {agent_input.get('Title', '')}",
        f"MCAT: {agent_input.get('MCAT', '')}",
        f"MCAT_id: {agent_input.get('MCAT_id', '')}",
        f"PRODUCTS_ENQUIRED: {json.dumps(agent_input.get('PRODUCTS_ENQUIRED', []), ensure_ascii=False)}",
        f"Products_Enquired_Count: {agent_input.get('Products_Enquired_Count', 0)}",
    ])
    sub.append({"seq": 3, "node": "buyer_classify", "fn": "build_user_message",
                "input": {"keys": list(agent_input.keys())},
                "output": user_text})

    llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url, timeout=timeout)
    sub.append({"seq": 4, "node": "buyer_classify", "fn": "ChatOpenAI.ainvoke",
                "input": {"model": model, "base_url": base_url,
                          "system_chars": len(system_prompt), "user_chars": len(user_text)},
                "output": None})
    response = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_text)])
    raw_output = str(response.content)
    sub[-1]["output"] = {"response_chars": len(raw_output), "preview": raw_output[:300]}

    result = _clean_classifier_output(raw_output)
    sub.append({"seq": 5, "node": "buyer_classify", "fn": "_clean_classifier_output",
                "input": {"raw_chars": len(raw_output)},
                "output": result})

    return {
        "system_prompt": system_prompt,
        "user_message": user_text,
        "raw_output": raw_output,
        "result": result,
        "classify_sub_steps": sub,
    }


def _build_graph():
    graph = StateGraph(BuyerProfileState)
    graph.add_node("prepare_input", _prepare_input)
    graph.add_node("buyer_classify", _buyer_classify)
    graph.set_entry_point("prepare_input")
    graph.add_edge("prepare_input", "buyer_classify")
    graph.add_edge("buyer_classify", END)
    return graph.compile()


@lru_cache(maxsize=1)
def _compiled_graph():
    return _build_graph()


async def run_buyer_profile_agent(
    offer_id: str,
    buylead_response: Dict[str, Any],
    _trace: bool = False,
) -> Dict[str, Any]:
    state = await _compiled_graph().ainvoke(
        {
            "offer_id": offer_id,
            "buylead_response": buylead_response,
        }
    )
    if _trace:
        return {
            "result": state["result"],
            "agent_input": state.get("agent_input", {}),
            "raw_output": state.get("raw_output", ""),
            "system_prompt": state.get("system_prompt", ""),
            "user_message": state.get("user_message", ""),
            "sub_steps": state.get("prepare_sub_steps", []) + state.get("classify_sub_steps", []),
        }
    return state["result"]
