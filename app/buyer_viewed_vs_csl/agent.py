"""Buyer Viewed vs CSL Agent — deterministic, no LLM.

Checks whether the buyer's enquired products (PRODUCTS_ENQUIRED from the BuyLead)
match anything in their recent CSL activity log (searches, browses, enquiries, buyLeads).

Input:
    offer_id         — BuyLead offer ID (for logging)
    buylead_response — raw BuyLead API response dict
    buyer_activity   — parsed result from parse_buyer_activity() OR raw API response

Output:
    {"status": "matched" | "not matched", "reason": str}
    On error, adds "error" key and returns status "not matched".
"""
import logging
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List
from urllib.parse import unquote

log = logging.getLogger("bl-auditor.buyer_viewed_vs_csl")

STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "or", "with", "to", "in", "on",
    "by", "is", "are", "type", "types", "number", "no", "set", "sets",
}

ACTIVITY_LABELS = {
    "ENQ": "an enquiry",
    "BL": "a buylead",
    "Browse": "a product browse",
    "Search": "a search",
}

MATCH_THRESHOLD = 0.5


def _normalize(text: Any) -> str:
    if not text or text == "-":
        return ""
    text = unquote(str(text))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokens(text: str) -> set:
    normalized = _normalize(text)
    return {w for w in normalized.split() if len(w) > 2 and w not in STOPWORDS}


def _format_datetime(raw: Any) -> str:
    try:
        return datetime.strptime(str(raw), "%Y%m%d%H%M%S").strftime("%d %b %Y, %I:%M %p")
    except (ValueError, TypeError):
        return str(raw) if raw else "an unknown time"


def _build_signals(buyer_activity: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten buyer_activity into comparable text signals.

    Handles two shapes:
      - Raw API response: {"DETAILS": [{keyword, glcat_mcat_name, mcat_names, ...}]}
      - Parsed response:  {"events":  [{keyword, mcat_name, activity_type, datetime, ...}]}
    """
    entries = (buyer_activity or {}).get("DETAILS")
    if entries is None:
        entries = (buyer_activity or {}).get("events")

    signals = []
    for entry in entries or []:
        texts: set = set()

        keyword = _normalize(entry.get("keyword"))
        if keyword:
            texts.add(keyword)

        # Raw API shape
        glcat_mcat_name = _normalize(entry.get("glcat_mcat_name"))
        if glcat_mcat_name:
            texts.add(glcat_mcat_name)

        mcat_names = entry.get("mcat_names")
        if isinstance(mcat_names, list):
            for name in mcat_names:
                norm_name = _normalize(name)
                if norm_name:
                    texts.add(norm_name)
        elif mcat_names:
            texts.add(_normalize(mcat_names))

        # Parsed shape: mcat_name is a single pre-resolved string
        mcat_name = _normalize(entry.get("mcat_name"))
        if mcat_name:
            texts.add(mcat_name)

        if not texts:
            continue

        signals.append({
            "texts": texts,
            "activity_type": entry.get("activity_type", "activity"),
            "datetime": entry.get("datetime"),
        })
    return signals


def _best_match(item_name: str, signals: List[Dict[str, Any]]):
    item_tokens = _tokens(item_name)
    item_norm = _normalize(item_name)
    if not item_tokens:
        return None

    best = None
    for signal in signals:
        for text in signal["texts"]:
            sig_tokens = {w for w in text.split() if len(w) > 2 and w not in STOPWORDS}
            if not sig_tokens:
                continue

            overlap = item_tokens & sig_tokens
            overlap_ratio = len(overlap) / len(item_tokens)
            substring_hit = bool(item_norm) and (item_norm in text or text in item_norm)
            similarity = SequenceMatcher(None, item_norm, text).ratio()

            score = max(overlap_ratio, similarity)
            if substring_hit:
                score = max(score, 0.9)

            if best is None or score > best[0]:
                best = (score, signal, text, overlap)

    return best


def _match_one(item_name: str, signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    best = _best_match(item_name, signals)

    if best and best[0] >= MATCH_THRESHOLD:
        score, signal, matched_text, overlap = best
        activity_label = ACTIVITY_LABELS.get(signal["activity_type"], signal["activity_type"] or "an activity")
        when = _format_datetime(signal["datetime"])
        shared = ", ".join(sorted(overlap)) if overlap else matched_text
        return {
            "status": "matched",
            "reason": (
                f"'{item_name}' matches buyer intent for '{matched_text}' via {activity_label} "
                f"on {when} (overlap: {shared})"
            ),
        }

    return {
        "status": "not matched",
        "reason": f"'{item_name}' has no overlapping search, browse, enquiry, or buylead activity",
    }


def _extract_product_enq(buylead_response: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {})
    products = []
    for p in data.get("PRODUCTS_ENQUIRED") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("FK_PC_ITEM_NAME") or p.get("FK_PC_ITEM_DISPLAY_NAME") or "").strip()
        if name:
            products.append({"FK_PC_ITEM_NAME": name})
    return products


def run_buyer_viewed_vs_csl(
    offer_id: str,
    buylead_response: Dict[str, Any],
    buyer_activity: Any,
) -> Dict[str, Any]:
    """Match buyer-enquired products against the buyer's CSL activity signals.

    Non-fatal: exceptions are caught and returned as error keys.
    Returns {"status": "matched"|"not matched", "reason": str}.
    """
    try:
        if not buyer_activity or not (buyer_activity or {}).get("ok", True):
            return {
                "status": "not matched",
                "reason": "No buyer activity data available to match against.",
            }

        product_enq = _extract_product_enq(buylead_response)
        if not product_enq:
            return {
                "status": "not matched",
                "reason": "No enquired products available to check buyer activity against.",
            }

        signals = _build_signals(buyer_activity)
        if not signals:
            return {
                "status": "not matched",
                "reason": "Buyer activity log is empty — no signals to match against.",
            }

        per_product = [_match_one(p["FK_PC_ITEM_NAME"], signals) for p in product_enq]

        overall_status = "matched" if any(p["status"] == "matched" for p in per_product) else "not matched"
        reason = "; ".join(p["reason"] for p in per_product)
        return {"status": overall_status, "reason": reason}

    except Exception as exc:
        log.warning("buyer_viewed_vs_csl error for offer_id=%s: %s", offer_id, exc)
        return {
            "status": "not matched",
            "reason": "Agent encountered an error.",
            "error": str(exc),
        }
