import json
from copy import deepcopy
from typing import Any, Dict

import httpx

BUYLEAD_API_URL = "https://leads.imutils.com/wservce/buyleads/detail/"
TIMEOUT = 60.0

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
    "modid": "GLADMIN",
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
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.get(BUYLEAD_API_URL, params=params)
        response.raise_for_status()
        return response.json()


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


def build_audit_payload_from_buylead(offer_id: str, buylead_response: Dict[str, Any]) -> Dict[str, Any]:
    data = buylead_response.get("RESPONSE", {}).get("DATA", {})
    payload = deepcopy(DEFAULT_AUDIT_PAYLOAD)

    payload["ISQ"] = parse_enrichmentinfo_to_isq(data.get("ENRICHMENTINFO"))
    payload["mcat_pool"] = []
    payload["img_url"] = ""
    payload["pc_item_id"] = int(offer_id)
    payload["item_name"] = data.get("ETO_OFR_TITLE") or payload["item_name"]
    payload["item_desc"] = data.get("ETO_OFR_DESC") or ""
    payload["mcat_name"] = data.get("PRIME_MCAT_NAME") or payload["mcat_name"]
    payload["mcat_id"] = data.get("MCAT_IDS") or payload["mcat_id"]

    price = data.get("ETO_OFR_APPROX_ORDER_VALUE")
    if price in (None, ""):
        payload["price"] = 0
    else:
        try:
            payload["price"] = float(price)
        except (TypeError, ValueError):
            payload["price"] = 0

    return payload
