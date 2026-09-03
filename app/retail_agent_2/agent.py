"""Retail Agent 2 — async wrapper around AuditorLLM.

Prompts (system, few-shots, user template, result format) all live in
co-located .md files and are loaded by `auditor_llm.py` at import time.
This wrapper only adapts the synchronous classifier to the BL Auditor's
async pipeline.
"""

import asyncio
import json
import re
from dataclasses import asdict
from typing import Any, Dict, Tuple

from .auditor_llm import (
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_USER_TEMPLATE,
    DEFAULT_RESULT_FORMAT,
    AuditorLLM,
)


def _parse_isq(enrichmentinfo: Any) -> Dict[str, str]:
    """Parse ENRICHMENTINFO into a {lowercased desc: response} map."""
    if enrichmentinfo in (None, ""):
        return {}
    try:
        parsed = json.loads(enrichmentinfo) if isinstance(enrichmentinfo, str) else enrichmentinfo
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    items = parsed.get("1", []) if isinstance(parsed, dict) else []
    out: Dict[str, str] = {}
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            desc = str(item.get("DESC") or "").strip().lower()
            resp = str(item.get("RESPONSE") or "").strip()
            if desc:
                out[desc] = resp
    return out


def _split_qty_unit(text: str) -> Tuple[float, str]:
    """Parse '16 Piece' → (16.0, 'Piece'). '50 kg' → (50.0, 'kg').

    Returns (0.0, "") if no leading number is found.
    """
    if not text:
        return 0.0, ""
    match = re.match(r"\s*([\d,]+(?:\.\d+)?)\s*(.*)$", text)
    if not match:
        return 0.0, text.strip()
    try:
        qty = float(match.group(1).replace(",", ""))
    except ValueError:
        qty = 0.0
    return qty, match.group(2).strip()


def _extract_inputs(buylead_response: Dict[str, Any]) -> Dict[str, Any]:
    data = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {}) or {}

    product = (
        data.get("PRIME_MCAT_NAME")
        or data.get("ETO_OFR_GLCAT_MCAT_NAME")
        or data.get("ETO_OFR_TITLE")
        or "Unknown"
    )

    isq = _parse_isq(data.get("ENRICHMENTINFO"))

    # Quantity / unit — prefer ISQ "Quantity" (e.g. "16 Piece") over top-level
    # ETO_OFR_QTY which is usually null in BL responses.
    qty_text = next((v for k, v in isq.items() if "quantity" in k), "")
    if qty_text:
        quantity, unit = _split_qty_unit(qty_text)
    else:
        qty_raw = data.get("ETO_OFR_QTY")
        try:
            quantity = float(qty_raw) if qty_raw not in (None, "") else 0.0
        except (TypeError, ValueError):
            quantity = 0.0
        unit = ""
    if not unit:
        unit = data.get("ETO_OFR_QTY_UNIT") or ""

    # Price — prefer ISQ "Order Value" / "Probable Order Value" (e.g. "Rs. 80 - 100").
    price_text = next((v for k, v in isq.items() if "order value" in k), "")
    if not price_text:
        price_raw = data.get("ETO_OFR_APPROX_ORDER_VALUE")
        price_text = str(price_raw) if price_raw not in (None, "") else ""
    price_range = price_text or "N/A"

    return {
        "product": product,
        "quantity": quantity,
        "unit": unit,
        "price_range": price_range,
    }


def _build_user_message(inputs: Dict[str, Any]) -> str:
    try:
        return DEFAULT_USER_TEMPLATE.format(
            product=inputs["product"],
            quantity=inputs["quantity"],
            unit=inputs["unit"],
            price_range=inputs["price_range"],
            median_context="",
            result_format=DEFAULT_RESULT_FORMAT,
        )
    except (KeyError, IndexError, ValueError):
        # Mirrors the fallback in AuditorLLM.analyze_lead so the trace shows what
        # was actually sent to the LLM if the template can't be format()-ed.
        return (
            f"Analyze this order:\n"
            f"- Product: {inputs['product']}\n"
            f"- Quantity: {inputs['quantity']} {inputs['unit']}\n"
            f"- Price Info: {inputs['price_range']}\n\n"
            f"Format:\n{DEFAULT_RESULT_FORMAT}"
        )


def _isq_quantity_missing_result(offer_id: str) -> Dict[str, Any]:
    return {
        "Display_id": offer_id,
        "Classification": "Non-Retail",
        "Confidence": "High",
        "Reason": "Hard override: Quantity is missing from ISQ — cannot classify, treated as non-retail.",
    }


async def run_retail_agent_2(
    offer_id: str,
    buylead_response: Dict[str, Any],
    _trace: bool = False,
) -> Dict[str, Any]:
    from app.services.buylead_service import isq_has_quantity
    if not isq_has_quantity(buylead_response):
        result = _isq_quantity_missing_result(offer_id)
        if not _trace:
            return result
        return {
            "result": result,
            "agent_input": {"_override": "ISQ Quantity missing"},
            "raw_output": json.dumps(result, ensure_ascii=False),
            "system_prompt": "(skipped — ISQ Quantity missing)",
            "user_message": "(skipped — ISQ Quantity missing)",
        }

    inputs = _extract_inputs(buylead_response)
    auditor = AuditorLLM()

    audit_result = await asyncio.to_thread(
        auditor.analyze_lead,
        inputs["product"],
        inputs["quantity"],
        inputs["unit"],
        inputs["price_range"],
    )

    result = {
        "Display_id": offer_id,
        "Classification": audit_result.classification,
        "Confidence": audit_result.confidence,
        "Reason": audit_result.reasoning,
    }

    if not _trace:
        return result

    try:
        from app.services.prompt_override_service import get_active_prompt
        active_system_prompt = get_active_prompt("retail_agent_2")[0]
    except Exception:
        active_system_prompt = DEFAULT_SYSTEM_PROMPT

    return {
        "result": result,
        "agent_input": inputs,
        "raw_output": json.dumps(asdict(audit_result), ensure_ascii=False),
        "system_prompt": active_system_prompt,
        "user_message": _build_user_message(inputs),
    }
