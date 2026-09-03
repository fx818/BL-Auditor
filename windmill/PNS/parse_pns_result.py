import json


def parse_row(row: dict) -> dict:
    base = {
        "user_glid": row.get("user_glid"),
        "user_role": row.get("user_role"),
        "src_mcat_id": row.get("src_mcat_id"),
        "created_at": row.get("fel.created_at") or row.get("created_at"),
    }

    raw = row.get("llm_extracted_json_masked") or ""
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return {**base, "parse_error": True}

    meta = d.get("metadata") or {}
    lead_tag = d.get("lead_tag") or {}
    call_type = meta.get("call_type") or {}
    buyer_intent = meta.get("buyer_intent") or {}
    call_outcome = meta.get("call_outcome") or {}
    additional = meta.get("additional_details") or {}
    next_steps = d.get("next_steps") or {}

    products = [
        {
            "product_name": p.get("product_name"),
            "quantity": p.get("quantity_required") or p.get("quantity") or p.get("Quantity") or 0,
            "specifications": [
                {k: v for k, v in s.items() if k not in ("buyer_requested", "seller_mentioned")}
                for s in (p.get("specifications") or [])
            ],
        }
        for p in (d.get("products") or [])
    ]

    return {
        **base,
        "callback": d.get("callback"),
        "deal_readiness": lead_tag.get("deal_readiness"),
        "deal_readiness_reason": lead_tag.get("deal_readiness_reason"),
        "deal_blockers": lead_tag.get("deal_blockers"),
        "call_type": call_type.get("type"),
        "call_type_reason": call_type.get("reason"),
        "repeat_buyer": (call_type.get("evidence") or {}).get("repeat_buyer"),
        "buyer_intent_level": buyer_intent.get("intent_level"),
        "buyer_intent_narrative": buyer_intent.get("narrative"),
        "buyer_intent_reasoning": buyer_intent.get("reasoning"),
        "call_outcome_category": call_outcome.get("category"),
        "call_outcome_notes": call_outcome.get("conclusion_notes"),
        "call_purpose": meta.get("call_purpose"),
        "primary_language": meta.get("primary_language"),
        "all_languages": meta.get("all_languages"),
        "intended_application": meta.get("intended_application"),
        "buyer_queries": additional.get("buyer_queries"),
        "seller_queries": additional.get("seller_queries"),
        "products": products,
        "buyer_next_steps": next_steps.get("buyer_next_steps"),
        "seller_next_steps": next_steps.get("seller_next_steps"),
    }


def aggregate_products(parsed_rows: list) -> list:
    products = []
    for row in parsed_rows:
        for p in (row.get("products") or []):
            products.append(p)
    return products


def main(rows: list = []) -> list:
    return [parse_row(r) for r in rows]
