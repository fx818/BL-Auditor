import csv
import os
from datetime import datetime
from typing import Any, Dict, Optional


CSV_HEADERS = [
    "logged_at",
    "offer_id",
    "trace_id",
    "item_name",
    "item_desc",
    "mcat_name",
    "price",
    "existing_retail_flag",
    "specs_category_outlier_status",
    "specs_category_outlier_reason",
    "title_category_outlier_status",
    "title_category_outlier_reason",
    "title_spec_verdict",
    "title_spec_verdict_reason",
    "buyer_viewed_status",
    "buyer_viewed_reason",
    "buyer_viewed_product_matched",
    "buyer_viewed_error",
    "retail_agent_2_classification",
    "retail_agent_2_confidence",
    "retail_agent_2_reason",
    "retail_agent_2_error",
    "isq_status",
    "isq_score",
    "isq_confidence",
    "isq_reason",
    "isq_error",
    "desc_status",
    "desc_reason",
    "desc_error",
    "specs_vs_cat_a2_status",
    "specs_vs_cat_a2_score",
    "specs_vs_cat_a2_confidence",
    "specs_vs_cat_a2_reason",
    "specs_vs_cat_a2_error",
    "title_vs_cat_a2_status",
    "title_vs_cat_a2_score",
    "title_vs_cat_a2_confidence",
    "title_vs_cat_a2_reason",
    "title_vs_cat_a2_error",
    "title_vs_specs_a2_status",
    "title_vs_specs_a2_score",
    "title_vs_specs_a2_confidence",
    "title_vs_specs_a2_reason",
    "title_vs_specs_a2_error",
    "bl_score",
    "bl_verdict",
    "bl_score_2",
    "bl_verdict_2",
    # S1 per-agent scoring breakdown (bw=base_weight, aw=adjusted_weight, sc=score%)
    "s1_specs_cat_bw", "s1_specs_cat_aw", "s1_specs_cat_sc",
    "s1_title_cat_bw", "s1_title_cat_aw", "s1_title_cat_sc",
    "s1_title_specs_bw", "s1_title_specs_aw", "s1_title_specs_sc",
    "s1_isq_bw", "s1_isq_aw", "s1_isq_sc",
    "s1_retail_bw", "s1_retail_aw", "s1_retail_sc",
    "s1_desc_bw", "s1_desc_aw", "s1_desc_sc",
    # S2 per-agent scoring breakdown
    "s2_specs_cat_bw", "s2_specs_cat_aw", "s2_specs_cat_sc",
    "s2_title_cat_bw", "s2_title_cat_aw", "s2_title_cat_sc",
    "s2_title_specs_bw", "s2_title_specs_aw", "s2_title_specs_sc",
    "s2_isq_bw", "s2_isq_aw", "s2_isq_sc",
    "s2_retail_bw", "s2_retail_aw", "s2_retail_sc",
    "s2_desc_bw", "s2_desc_aw", "s2_desc_sc",
    # BL Completeness Score
    "completeness_score",
    "completeness_pct",
    "cs_title",
    "cs_quantity",
    "cs_spec_count",
    "cs_predicted",
    "cs_desc",
    "bl_quality_1",
    "bl_quality_2",
    "bl_lq_completeness",
    "bl_high_value",
    # LLM-selected keywords from buyer activity (only for "Buyer Searched for"
    # descriptions whose desc verdict is Incorrect); comma-separated.
    "activity_keywords",
    # Deterministic Quantity Audit (no LLM): Absurd / OK / Unverifiable + reason.
    "quantity_audit_status",
    "quantity_audit_reason",
    # Buyer Profile Agent (standalone — BL-title coherence auditor, not part of BL Score 1/2).
    "buyer_profile_status",         # "related" | "not related" (LLM coherence)
    "buyer_profile_reasoning",
    "buyer_profile_completeness",   # "Complete" | "Incomplete" (deterministic)
    "buyer_profile_check_reason",
    "buyer_profile_tenure",         # "New" | "Old" | "Unknown" (deterministic)
    "buyer_profile_error",
    # Buyer Viewed vs CSL Agent (deterministic — product-enquiry vs. CSL activity-log match).
    "buyer_viewed_vs_csl_status",
    "buyer_viewed_vs_csl_reason",
    "buyer_viewed_vs_csl_error",
    # Email of the user who ran the audit; "admin" for consumer/worker runs.
    "audit_done_by",
    # Recheck agent — second-opinion LLM call for flagged Windmill agents.
    "recheck_ran",
    "title_vs_cat_recheck_before",
    "title_vs_cat_recheck_verdict",
    "title_vs_cat_recheck_reason",
    "title_vs_specs_recheck_before",
    "title_vs_specs_recheck_verdict",
    "title_vs_specs_recheck_reason",
    "specs_vs_cat_recheck_before",
    "specs_vs_cat_recheck_verdict",
    "specs_vs_cat_recheck_reason",
    "isq_recheck_before",
    "isq_recheck_verdict",
    "isq_recheck_reason",
    # Decision Agent — deterministic final verdict from 4 Windmill agent statuses + recheck.
    "decision_verdict",
    "decision_reason",
]

_DATA_DIR = os.environ.get("DATA_DIR") or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CSV_PATH = os.path.join(_DATA_DIR, "audit_dashboard_log.csv")


def price_view(pr: Dict[str, Any]) -> Dict[str, str]:
    """Project the price agent's new-schema output onto the stable fields used by
    the CSV log, the batch SSE payload, and the result-page card.

    Returns string-valued view keys (never None) so consumers can render directly:
      verdict, confidence, score, ai_range_str, reason, error
    """
    pr = pr or {}
    rng = pr.get("ai_order_value_range") or {}
    low = rng.get("low")
    high = rng.get("high")
    try:
        if low not in (None, "") and high not in (None, ""):
            ai_range_str = f"\u20b9{int(round(float(low))):,} \u2013 \u20b9{int(round(float(high))):,}"
        elif low not in (None, ""):
            ai_range_str = f"\u20b9{int(round(float(low))):,}"
        elif high not in (None, ""):
            ai_range_str = f"Upto \u20b9{int(round(float(high))):,}"
        else:
            ai_range_str = ""
    except (TypeError, ValueError):
        ai_range_str = ""

    score_raw = (pr.get("alignment") or {}).get("overlap_pct_of_ai")
    if isinstance(score_raw, (int, float)):
        score_str = str(int(round(score_raw)))
    elif score_raw in (None, ""):
        score_str = ""
    else:
        score_str = str(score_raw)

    return {
        "verdict": str(pr.get("verdict", "") or ""),
        "confidence": str(pr.get("confidence", "") or ""),
        "score": score_str,
        "ai_range_str": ai_range_str,
        "reason": str(pr.get("reason", "") or ""),
        "error": str(pr.get("error", "") or ""),
    }


_SCORING_AGENT_KEYS = [
    ("specs_vs_category", "specs_cat"),
    ("title_vs_category", "title_cat"),
    ("title_vs_specs", "title_specs"),
    ("isq_validation", "isq"),
    ("retail_classification", "retail"),
    ("description_coherence", "desc"),
]


def _scoring_breakdown_fields(sr: Dict[str, Any], prefix: str) -> Dict[str, str]:
    breakdown = {
        ag["key"]: ag
        for ag in sr.get("agent_breakdown", [])
        if isinstance(ag, dict) and "key" in ag
    }
    fields: Dict[str, str] = {}
    for agent_key, short in _SCORING_AGENT_KEYS:
        ag = breakdown.get(agent_key, {})
        bw = ag.get("base_weight")
        aw = ag.get("adjusted_weight")
        sc = ag.get("agent_score")
        fields[f"{prefix}_{short}_bw"] = str(int(bw)) if bw is not None else ""
        fields[f"{prefix}_{short}_aw"] = str(round(float(aw), 2)) if aw is not None else ""
        fields[f"{prefix}_{short}_sc"] = str(int(round(float(sc) * 100))) if sc is not None else ""
    return fields


def _get_nested(source: Dict[str, Any], *keys: str) -> Any:
    value: Any = source
    for key in keys:
        if not isinstance(value, dict):
            return ""
        value = value.get(key, "")
    return value if value is not None else ""


def _ensure_csv_headers() -> None:
    if not os.path.exists(CSV_PATH):
        return

    with open(CSV_PATH, "r", newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        existing_headers = reader.fieldnames or []
        if existing_headers == CSV_HEADERS:
            return
        rows = list(reader)

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            normalized_row = {header: row.get(header, "") for header in CSV_HEADERS}
            writer.writerow(normalized_row)


def _parse_rupee_value(val: Any) -> Optional[float]:
    """Parse a rupee value from LLM output into a float.

    Handles: plain int/float, comma-formatted strings ("1,00,00,000"),
    lakh/crore shorthand ("1 crore", "5L", "2.5 lakh", "1cr"),
    and rupee-prefixed strings ("₹10,00,000").
    Returns None if unparseable.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("₹", "").replace(",", "").replace(" ", "").lower()
    # crore: 1cr | 1crore | 1.5crore
    import re as _re
    m = _re.match(r"^([\d.]+)(cr|crore)$", s)
    if m:
        return float(m.group(1)) * 1_00_00_000
    # lakh: 1l | 1lakh | 2.5lakh
    m = _re.match(r"^([\d.]+)(l|lakh|lac)$", s)
    if m:
        return float(m.group(1)) * 1_00_000
    # plain number
    try:
        return float(s)
    except ValueError:
        return None


def _compute_bl_high_value(price_response: Dict[str, Any]) -> str:
    ai_range = price_response.get("ai_order_value_range") or {}
    low = _parse_rupee_value(ai_range.get("low"))
    if low is not None and low >= 1_00_00_000:  # 1 crore
        return "HIGH VALUE BL"
    return ""


def _compute_bl_quality_1(
    audit_response: Dict[str, Any],
    isq_response: Dict[str, Any],
    desc_response: Dict[str, Any],
    payload: Dict[str, Any],
) -> str:
    if (audit_response.get("title_category_outlier") or {}).get("status", "") == "Wrong":
        return "FATAL"
    if not (payload.get("ISQ") or []):
        return "LOW QUALITY BL"
    if (audit_response.get("specs_category_outlier") or {}).get("status", "") == "Wrong":
        return "LOW QUALITY BL"
    if (desc_response or {}).get("status", "") == "outlier":
        return "LOW QUALITY BL"
    if (isq_response or {}).get("status", "") == "outlier":
        return "LOW QUALITY BL"
    return ""


def _compute_bl_quality_2(
    tca2_response: Dict[str, Any],
    specs_a2_response: Dict[str, Any],
    isq_response: Dict[str, Any],
    desc_response: Dict[str, Any],
    payload: Dict[str, Any],
) -> str:
    tca2_status = (tca2_response or {}).get("Status", "") or (tca2_response or {}).get("status", "")
    if tca2_status == "Incorrect":
        return "FATAL"
    if not (payload.get("ISQ") or []):
        return "LOW QUALITY BL"
    specs_a2_status = (specs_a2_response or {}).get("Status", "") or (specs_a2_response or {}).get("status", "")
    if specs_a2_status == "Incorrect":
        return "LOW QUALITY BL"
    if (desc_response or {}).get("status", "") == "outlier":
        return "LOW QUALITY BL"
    if (isq_response or {}).get("status", "") == "outlier":
        return "LOW QUALITY BL"
    return ""


def _compute_bl_lq_completeness(completeness_response: Dict[str, Any]) -> str:
    pct = (completeness_response or {}).get("percentage")
    if pct is not None and pct <= 50:
        return "LOW QUALITY BL"
    return ""


_RECHECK_AGENT_TO_COL = {
    "Title vs MCAT Agent":  "title_vs_cat",
    "Title ISQ Agent":      "title_vs_specs",
    "ISQ vs MCAT Agent":    "specs_vs_cat",
    "Inter ISQ Agent":      "isq",
}
_RECHECK_BEFORE_KEY = {
    "title_vs_cat":  "title_category_outlier",
    "title_vs_specs": "title_spec_verdict",
    "specs_vs_cat":  "specs_category_outlier",
    "isq":           "isq",
}


def _recheck_verdict_fields(recheck_data: Dict[str, Any]) -> Dict[str, str]:
    before = recheck_data.get("before") or {}
    results_by_col = {
        _RECHECK_AGENT_TO_COL[r["agent"]]: r
        for r in recheck_data.get("results") or []
        if isinstance(r, dict) and r.get("agent") in _RECHECK_AGENT_TO_COL
    }
    out: Dict[str, str] = {}
    for col, before_key in _RECHECK_BEFORE_KEY.items():
        out[f"{col}_recheck_before"] = before.get(before_key, "")
        rc = results_by_col.get(col) or {}
        out[f"{col}_recheck_verdict"] = rc.get("status", "")
        out[f"{col}_recheck_reason"] = rc.get("reason", "")
    return out


def append_audit_dashboard_row(
    offer_id: str,
    payload: Dict[str, Any],
    audit_response: Dict[str, Any],
    buyer_response: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    existing_retail_flag: str = "",
    retail_agent_2_response: Optional[Dict[str, Any]] = None,
    isq_validation_response: Optional[Dict[str, Any]] = None,
    description_response: Optional[Dict[str, Any]] = None,
    specs_vs_category_agent2_response: Optional[Dict[str, Any]] = None,
    title_vs_category_agent2_response: Optional[Dict[str, Any]] = None,
    title_vs_specs_agent2_response: Optional[Dict[str, Any]] = None,
    scoring_response: Optional[Dict[str, Any]] = None,
    scoring_response_2: Optional[Dict[str, Any]] = None,
    completeness_response: Optional[Dict[str, Any]] = None,
    activity_keywords: str = "",
    quantity_audit_response: Optional[Dict[str, Any]] = None,
    buyer_profile_response: Optional[Dict[str, Any]] = None,
    buyer_viewed_vs_csl_response: Optional[Dict[str, Any]] = None,
    audit_done_by: str = "admin",
    recheck_data: Optional[Dict[str, Any]] = None,
    decision_response: Optional[Dict[str, Any]] = None,
) -> str:
    _ensure_csv_headers()
    buyer_response = buyer_response or {}
    retail_agent_2_response = retail_agent_2_response or {}
    isq_validation_response = isq_validation_response or {}
    description_response = description_response or {}
    specs_vs_category_agent2_response = specs_vs_category_agent2_response or {}
    title_vs_category_agent2_response = title_vs_category_agent2_response or {}
    title_vs_specs_agent2_response = title_vs_specs_agent2_response or {}
    scoring_response = scoring_response or {}
    scoring_response_2 = scoring_response_2 or {}
    completeness_response = completeness_response or {}
    quantity_audit_response = quantity_audit_response or {}
    buyer_profile_response = buyer_profile_response or {}
    buyer_viewed_vs_csl_response = buyer_viewed_vs_csl_response or {}

    row = {
        "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "offer_id": offer_id,
        "trace_id": trace_id or "",
        "item_name": audit_response.get("item_name") or payload.get("item_name", ""),
        "item_desc": payload.get("item_desc", "") or "",
        "mcat_name": payload.get("mcat_name", ""),
        "price": payload.get("price", ""),
        "existing_retail_flag": existing_retail_flag or "",
        "specs_category_outlier_status": _get_nested(audit_response, "specs_category_outlier", "status"),
        "specs_category_outlier_reason": _get_nested(audit_response, "specs_category_outlier", "reason"),
        "title_category_outlier_status": _get_nested(audit_response, "title_category_outlier", "status"),
        "title_category_outlier_reason": _get_nested(audit_response, "title_category_outlier", "reason"),
        "title_spec_verdict": _get_nested(audit_response, "title_spec_verdict", "status"),
        "title_spec_verdict_reason": _get_nested(audit_response, "title_spec_verdict", "reason"),
        "buyer_viewed_status": buyer_response.get("status", ""),
        "buyer_viewed_reason": buyer_response.get("reason", ""),
        "buyer_viewed_product_matched": buyer_response.get("product_matched", ""),
        "buyer_viewed_error": buyer_response.get("error", ""),
        "retail_agent_2_classification": retail_agent_2_response.get("Classification", ""),
        "retail_agent_2_confidence": retail_agent_2_response.get("Confidence", ""),
        "retail_agent_2_reason": retail_agent_2_response.get("Reason", ""),
        "retail_agent_2_error": retail_agent_2_response.get("error", ""),
        "isq_status": isq_validation_response.get("status", ""),
        "isq_score": "",
        "isq_confidence": "",
        "isq_reason": isq_validation_response.get("reason", ""),
        "isq_error": isq_validation_response.get("error", ""),
        "desc_status": description_response.get("status", ""),
        "desc_reason": description_response.get("reason", ""),
        "desc_error": description_response.get("error", ""),
        "specs_vs_cat_a2_status": specs_vs_category_agent2_response.get("Status", "") or specs_vs_category_agent2_response.get("status", ""),
        "specs_vs_cat_a2_score": specs_vs_category_agent2_response.get("Score", specs_vs_category_agent2_response.get("score", "")) if (specs_vs_category_agent2_response.get("Score") is not None or specs_vs_category_agent2_response.get("score") is not None) else "",
        "specs_vs_cat_a2_confidence": specs_vs_category_agent2_response.get("Confidence", "") or specs_vs_category_agent2_response.get("confidence", ""),
        "specs_vs_cat_a2_reason": specs_vs_category_agent2_response.get("Reason", "") or specs_vs_category_agent2_response.get("reasoning", ""),
        "specs_vs_cat_a2_error": specs_vs_category_agent2_response.get("error", ""),
        "title_vs_cat_a2_status": title_vs_category_agent2_response.get("Status", "") or title_vs_category_agent2_response.get("status", ""),
        "title_vs_cat_a2_score": title_vs_category_agent2_response.get("Score", title_vs_category_agent2_response.get("score", "")) if (title_vs_category_agent2_response.get("Score") is not None or title_vs_category_agent2_response.get("score") is not None) else "",
        "title_vs_cat_a2_confidence": title_vs_category_agent2_response.get("Confidence", "") or title_vs_category_agent2_response.get("confidence", ""),
        "title_vs_cat_a2_reason": title_vs_category_agent2_response.get("Reason", "") or title_vs_category_agent2_response.get("reasoning", ""),
        "title_vs_cat_a2_error": title_vs_category_agent2_response.get("error", ""),
        "title_vs_specs_a2_status": title_vs_specs_agent2_response.get("Status", "") or title_vs_specs_agent2_response.get("status", ""),
        "title_vs_specs_a2_score": title_vs_specs_agent2_response.get("Score", title_vs_specs_agent2_response.get("score", "")) if (title_vs_specs_agent2_response.get("Score") is not None or title_vs_specs_agent2_response.get("score") is not None) else "",
        "title_vs_specs_a2_confidence": title_vs_specs_agent2_response.get("Confidence", "") or title_vs_specs_agent2_response.get("confidence", ""),
        "title_vs_specs_a2_reason": title_vs_specs_agent2_response.get("Reason", "") or title_vs_specs_agent2_response.get("reasoning", ""),
        "title_vs_specs_a2_error": title_vs_specs_agent2_response.get("error", ""),
        "bl_score": scoring_response.get("composite_score", "") if scoring_response.get("composite_score") is not None else "",
        "bl_verdict": scoring_response.get("verdict", ""),
        "bl_score_2": scoring_response_2.get("composite_score", "") if scoring_response_2.get("composite_score") is not None else "",
        "bl_verdict_2": scoring_response_2.get("verdict", ""),
        **_scoring_breakdown_fields(scoring_response, "s1"),
        **_scoring_breakdown_fields(scoring_response_2, "s2"),
        "completeness_score": str(completeness_response.get("total_score", "")) if completeness_response.get("total_score") is not None else "",
        "completeness_pct": str(completeness_response.get("percentage", "")) if completeness_response.get("percentage") is not None else "",
        "cs_title": str((completeness_response.get("breakdown") or {}).get("title", {}).get("score", "")) if (completeness_response.get("breakdown") or {}).get("title") is not None else "",
        "cs_quantity": str((completeness_response.get("breakdown") or {}).get("quantity", {}).get("score", "")) if (completeness_response.get("breakdown") or {}).get("quantity") is not None else "",
        "cs_spec_count": str((completeness_response.get("breakdown") or {}).get("spec_count", {}).get("score", "")) if (completeness_response.get("breakdown") or {}).get("spec_count") is not None else "",
        "cs_predicted": str((completeness_response.get("breakdown") or {}).get("predicted_specs", {}).get("score", "")) if (completeness_response.get("breakdown") or {}).get("predicted_specs") is not None else "",
        "cs_desc": str((completeness_response.get("breakdown") or {}).get("description", {}).get("score", "")) if (completeness_response.get("breakdown") or {}).get("description") is not None else "",
        "bl_quality_1": _compute_bl_quality_1(audit_response, isq_validation_response or {}, description_response or {}, payload),
        "bl_quality_2": _compute_bl_quality_2(title_vs_category_agent2_response or {}, specs_vs_category_agent2_response or {}, isq_validation_response or {}, description_response or {}, payload),
        "bl_lq_completeness": _compute_bl_lq_completeness(completeness_response),
        "bl_high_value": "",
        "activity_keywords": activity_keywords or "",
        "quantity_audit_status": quantity_audit_response.get("status", "") or "",
        "quantity_audit_reason": quantity_audit_response.get("reason", "") or "",
        "buyer_profile_status": buyer_profile_response.get("status", ""),
        "buyer_profile_reasoning": buyer_profile_response.get("reasoning", ""),
        "buyer_profile_completeness": buyer_profile_response.get("Profile_Status", ""),
        "buyer_profile_check_reason": buyer_profile_response.get("Profile_Check_Reason", ""),
        "buyer_profile_tenure": buyer_profile_response.get("Tenure", ""),
        "buyer_profile_error": buyer_profile_response.get("error", ""),
        "buyer_viewed_vs_csl_status": buyer_viewed_vs_csl_response.get("status", ""),
        "buyer_viewed_vs_csl_reason": buyer_viewed_vs_csl_response.get("reason", ""),
        "buyer_viewed_vs_csl_error": buyer_viewed_vs_csl_response.get("error", ""),
        "audit_done_by": audit_done_by or "admin",
        "recheck_ran": str((recheck_data or {}).get("ran", False)).lower(),
        **_recheck_verdict_fields(recheck_data or {}),
        "decision_verdict": (decision_response or {}).get("Final_Verdict", ""),
        "decision_reason": (decision_response or {}).get("Reason", ""),
    }

    file_exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    return CSV_PATH


def clear_audit_dashboard_log() -> None:
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as csv_file:
        csv.DictWriter(csv_file, fieldnames=CSV_HEADERS).writeheader()


def read_audit_dashboard_rows() -> list[Dict[str, str]]:
    _ensure_csv_headers()
    if not os.path.exists(CSV_PATH):
        return []

    with open(CSV_PATH, "r", newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        rows = []
        for raw in reader:
            row = {k: v for k, v in raw.items() if isinstance(k, str)}
            row["audit_done_by"] = row.get("audit_done_by") or "unknown"
            rows.append(row)
    rows.reverse()
    return rows
