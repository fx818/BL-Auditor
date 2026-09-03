import asyncio
import json
import logging
import os
from copy import deepcopy
from typing import Any, Dict

import httpx

log = logging.getLogger("bl-auditor.buylead_service")

BUYLEAD_API_URL = "http://leads.imutils.com/wservce/buyleads/detail/"
TIMEOUT = 60.0
_BUYLEAD_MAX_RETRIES = int(os.getenv("BUYLEAD_MAX_RETRIES", "2"))

DEFAULT_AUDIT_PAYLOAD: Dict[str, Any] = {
    "ISQ": [],
    "approval_status": 10,
    "custtype": "53",
    "display_name": "",
    "fk_mcat_type_id": "",
    "glcat_mcat_image_display": 1,
    "glid": 187147894,
    "image_id": "-1",
    "img_url": "",
    "item_desc": "Terminal Block Wire Connectors - Industrial grade connectors for electrical wiring.",
    "item_name": "Terminal Block Wire Connectors",
    "mcat_flag": "-1",
    "mcat_id": "39596",
    "mcat_name": "Terminal Block Connectors",
    "mcat_pool": [],
    "modid": "BL-Testing",
    "pc_item_id": 326487619,
    "price": 350,
    "rejection_code": 0,
    "screen_name": "live_product_approval",
    "secondary_mcats": [],
    "unit": "Piece",
    "worker_name": "Product_Approval_Auditor_1.2",
}


async def fetch_buylead_detail(offer_id: str) -> Dict[str, Any]:
    params = {
        "modid": "ETO",
        "offer_type": "B",
        "buyer_response": "2",
        "additionalinfo_format": "JSON",
        "token": "imobile@15061981",
        "breadcrumb": "1",
        "offer": offer_id,
    }
    last_exc: Exception | None = None
    for attempt in range(1, _BUYLEAD_MAX_RETRIES + 2):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(BUYLEAD_API_URL, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status != 429 and status < 500:
                raise  # 4xx (not 429): permanent error, no retry
            last_exc = exc
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
        # Any other exception propagates immediately

        log.warning(
            "BuyLead fetch offer_id=%s attempt %d/%d failed: %s",
            offer_id, attempt, _BUYLEAD_MAX_RETRIES + 1, last_exc,
        )
        if attempt <= _BUYLEAD_MAX_RETRIES:
            await asyncio.sleep(2 ** (attempt - 1))

    raise RuntimeError(
        f"BuyLead fetch failed after {_BUYLEAD_MAX_RETRIES + 1} attempts "
        f"for offer_id={offer_id}: {last_exc}"
    ) from last_exc


def parse_enrichmentinfo_to_isq(enrichmentinfo: Any) -> list[Dict[str, str]]:
    if not enrichmentinfo:
        return []

    try:
        parsed = json.loads(enrichmentinfo) if isinstance(enrichmentinfo, str) else enrichmentinfo
    except (TypeError, ValueError, json.JSONDecodeError):
        return []

    items = parsed.get("1", []) if isinstance(parsed, dict) else []
    isq = []
    for item in items:
        if not isinstance(item, dict):
            continue
        desc = item.get("DESC")
        response = item.get("RESPONSE")
        if not desc and not response:
            continue
        isq.append(
            {
                "IM_SPEC_MASTER_DESC": str(desc or ""),
                "ISQ_RESPONSE": str(response or ""),
            }
        )
    return isq


def isq_has_quantity(buylead_response: Dict[str, Any]) -> bool:
    """True if the BL's ISQ list has a 'Quantity'-like row with a non-empty value.

    Case-insensitive substring match on IM_SPEC_MASTER_DESC catches 'Quantity',
    'Order Quantity', 'Required Quantity', etc. — same convention as
    retail_agent_2._extract_inputs.
    """
    data = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {}) or {}
    for row in parse_enrichmentinfo_to_isq(data.get("ENRICHMENTINFO")):
        desc = str(row.get("IM_SPEC_MASTER_DESC", "")).strip().lower()
        resp = str(row.get("ISQ_RESPONSE", "")).strip()
        if "quantity" in desc and resp:
            return True
    return False


_RETAIL_ENQ_TYPES = {1, 3, 5, 6}


def map_existing_retail_flag(eto_enq_typ: Any) -> str:
    """Map BL ETO_ENQ_TYP to a human label. Null/missing -> 'null'.

    1, 3, 5, 6 -> 'Retail'. Any other value -> 'Non-Retail'.
    """
    if eto_enq_typ is None or eto_enq_typ == "":
        return "null"
    try:
        return "Retail" if int(eto_enq_typ) in _RETAIL_ENQ_TYPES else "Non-Retail"
    except (TypeError, ValueError):
        return "Non-Retail"


def extract_existing_retail_flag(buylead_response: Dict[str, Any]) -> str:
    data = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {}) or {}
    return map_existing_retail_flag(data.get("ETO_ENQ_TYP"))


def build_audit_payload_from_buylead(offer_id: str, buylead_response: Dict[str, Any]) -> Dict[str, Any]:
    data = buylead_response.get("RESPONSE", {}).get("DATA", {}) or {}
    payload = deepcopy(DEFAULT_AUDIT_PAYLOAD)

    payload["ISQ"] = parse_enrichmentinfo_to_isq(data.get("ENRICHMENTINFO"))
    payload["mcat_pool"] = []
    payload["img_url"] = ""
    payload["pc_item_id"] = int(offer_id)
    payload["item_name"] = data.get("ETO_OFR_TITLE") or payload["item_name"]
    payload["item_desc"] = data.get("ETO_OFR_DESC") or ""
    payload["mcat_name"] = data.get("PRIME_MCAT_NAME") or payload["mcat_name"]
    payload["mcat_id"] = data.get("FK_GLCAT_MCAT_ID") or payload["mcat_id"]

    price = data.get("ETO_OFR_APPROX_ORDER_VALUE")
    if price in (None, ""):
        payload["price"] = 0
    else:
        try:
            payload["price"] = float(price)
        except (TypeError, ValueError):
            payload["price"] = 0

    return payload
