"""Completeness Agent — deterministic BuyLead completeness scoring.

No LLM call — pure rule-based math. Evaluates 5 factors (title, quantity,
spec_count, predicted_specs, description) each scored 0–3.

Output contract:
    {
        "completeness_score": int (0–15),
        "completeness_pct": float (0.0–100.0),
        "factors": {str: int}
    }
"""
from __future__ import annotations

import csv
import json
import os
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# QTY_LESS_MCAT lookup — MCATs where quantity ISQ is not defined → score = 2
# ---------------------------------------------------------------------------
_CSV_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "QTY_LESS_MCAT.csv")
)

def _load_qty_less_mcats() -> set:
    result = set()
    if not os.path.exists(_CSV_PATH):
        return result
    with open(_CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("glcat_mcat_name", "")
            if name:
                result.add(name.strip().lower())
    return result

_QTY_LESS_MCATS: set = _load_qty_less_mcats()

_PREDICTED_DESCS = {"quantity", "probable order value", "probable requirement type"}
_EXCLUDED_ISQ_DESCS = _PREDICTED_DESCS


def _is_filled(val: Any) -> bool:
    return val is not None and str(val).strip() != ""


# ---------------------------------------------------------------------------
# Factor scorers
# ---------------------------------------------------------------------------

def _score_title(bl_title: str, mcat_name: str) -> Dict[str, Any]:
    t = (bl_title or "").strip()
    if not t:
        return {"score": 0, "max": 3, "reason": "Title is empty."}
    if len(t.split()) == 1:
        return {"score": 0, "max": 3, "reason": f"Title is a single word: '{t}'."}
    if t.lower() == (mcat_name or "").strip().lower():
        return {"score": 1, "max": 3, "reason": f"Title is identical to MCAT name: '{mcat_name}'."}
    return {"score": 3, "max": 3, "reason": f"Title '{t}' is descriptive and differs from MCAT."}


def _score_quantity(mcat_name: str, isq: List[Dict]) -> Dict[str, Any]:
    mcat_lower = (mcat_name or "").strip().lower()
    if mcat_lower in _QTY_LESS_MCATS:
        return {"score": 2, "max": 3, "reason": f"MCAT '{mcat_name}' does not have quantity defined."}
    for item in (isq or []):
        desc = (item.get("IM_SPEC_MASTER_DESC") or "").strip().lower()
        if desc == "quantity":
            resp = item.get("ISQ_RESPONSE")
            if _is_filled(resp):
                return {"score": 3, "max": 3, "reason": f"Quantity filled: '{resp}'."}
            return {"score": 1, "max": 3, "reason": "Quantity ISQ defined but not filled."}
    return {"score": 1, "max": 3, "reason": "Quantity ISQ not found in specs."}


def _score_spec_count(isq: List[Dict]) -> Dict[str, Any]:
    count = 0
    for item in (isq or []):
        desc = (item.get("IM_SPEC_MASTER_DESC") or "").strip().lower()
        if desc in _EXCLUDED_ISQ_DESCS:
            continue
        if _is_filled(item.get("ISQ_RESPONSE")):
            count += 1
    score = min(count, 3)
    return {
        "score": score,
        "max": 3,
        "reason": f"{count} non-predicted spec(s) filled (capped at 3).",
    }


def _score_predicted_specs(enrichmentinfo_raw: Optional[str]) -> Dict[str, Any]:
    if not enrichmentinfo_raw:
        return {"score": 0, "max": 3, "reason": "No enrichment info available."}
    try:
        data = json.loads(enrichmentinfo_raw)
        items = data.get("1", [])
    except (json.JSONDecodeError, AttributeError):
        return {"score": 0, "max": 3, "reason": "Could not parse enrichment info."}

    filled: Dict[str, bool] = {}
    for item in items:
        desc = (item.get("DESC") or "").strip().lower()
        resp = item.get("RESPONSE")
        if desc in _PREDICTED_DESCS:
            filled[desc] = _is_filled(resp)

    qty_filled = filled.get("quantity", False)
    pov_filled = filled.get("probable order value", False)
    prt_filled = filled.get("probable requirement type", False)

    if qty_filled and pov_filled and prt_filled:
        return {"score": 3, "max": 3, "reason": "Quantity, Probable Order Value and Probable Requirement Type all filled."}
    if qty_filled and (pov_filled or prt_filled):
        extra = "Probable Order Value" if pov_filled else "Probable Requirement Type"
        return {"score": 2, "max": 3, "reason": f"Quantity and {extra} filled."}
    if qty_filled:
        return {"score": 1, "max": 3, "reason": "Only Quantity filled in predicted specs."}
    return {"score": 0, "max": 3, "reason": "No predicted specs filled."}


def _score_description(description: Optional[str]) -> Dict[str, Any]:
    if _is_filled(description):
        return {"score": 3, "max": 3, "reason": "Description is filled."}
    return {"score": 0, "max": 3, "reason": "Description is empty or not provided."}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_completeness_agent(
    bl_title: str,
    mcat_name: str,
    isq: List[Dict],
    enrichmentinfo_raw: Optional[str],
    description: Optional[str],
) -> Dict[str, Any]:
    breakdown = {
        "title":           _score_title(bl_title, mcat_name),
        "quantity":        _score_quantity(mcat_name, isq),
        "spec_count":      _score_spec_count(isq),
        "predicted_specs": _score_predicted_specs(enrichmentinfo_raw),
        "description":     _score_description(description),
    }
    total = sum(v["score"] for v in breakdown.values())
    percentage = round(total / 15 * 100, 2)
    return {
        "total_score": round(total, 4),
        "percentage": percentage,
        "breakdown": breakdown,
    }
