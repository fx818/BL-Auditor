import csv
import os
from datetime import datetime
from typing import Any, Dict, Optional


CSV_HEADERS = [
    "logged_at",
    "offer_id",
    "trace_id",
    "item_name",
    "mcat_name",
    "price",
    "specs_category_outlier_status",
    "specs_category_outlier_reason",
    "title_category_outlier_status",
    "title_category_outlier_reason",
    "photo_category_outlier_status",
    "photo_category_outlier_reason",
    "photo_title_verdict",
    "photo_title_verdict_reason",
    "title_spec_verdict",
    "title_spec_verdict_reason",
    "price_flag_verdict",
    "price_flag_reason",
    "retail_classification",
    "retail_classi_score",
    "retail_confidence",
    "retail_override_applied",
    "retail_reason",
    "retail_error",
    "price_agent_result",
    "price_agent_score",
    "price_agent_confidence",
    "price_value_by_ai",
    "price_agent_reason",
    "price_error",
    "buyer_profile_genuineness",
    "buyer_profile_score",
    "buyer_profile_confidence",
    "buyer_profile_reason",
    "buyer_profile_error",
]

CSV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "audit_dashboard_log.csv"))


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


def append_audit_dashboard_row(
    offer_id: str,
    payload: Dict[str, Any],
    audit_response: Dict[str, Any],
    retail_response: Optional[Dict[str, Any]] = None,
    price_response: Optional[Dict[str, Any]] = None,
    buyer_response: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> str:
    _ensure_csv_headers()
    retail_response = retail_response or {}
    price_response = price_response or {}
    buyer_response = buyer_response or {}

    row = {
        "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "offer_id": offer_id,
        "trace_id": trace_id or "",
        "item_name": audit_response.get("item_name") or payload.get("item_name", ""),
        "mcat_name": payload.get("mcat_name", ""),
        "price": payload.get("price", ""),
        "specs_category_outlier_status": _get_nested(audit_response, "specs_category_outlier", "status"),
        "specs_category_outlier_reason": _get_nested(audit_response, "specs_category_outlier", "reason"),
        "title_category_outlier_status": _get_nested(audit_response, "title_category_outlier", "status"),
        "title_category_outlier_reason": _get_nested(audit_response, "title_category_outlier", "reason"),
        "photo_category_outlier_status": _get_nested(audit_response, "photo_category_outlier", "status"),
        "photo_category_outlier_reason": _get_nested(audit_response, "photo_category_outlier", "reason"),
        "photo_title_verdict": _get_nested(audit_response, "photo_title_verdict", "final_verdict"),
        "photo_title_verdict_reason": audit_response.get("photo_title_verdict_reason", "") or "",
        "title_spec_verdict": _get_nested(audit_response, "title_spec_verdict", "final_verdict"),
        "title_spec_verdict_reason": audit_response.get("title_spec_verdict_reason", "") or "",
        "price_flag_verdict": _get_nested(audit_response, "price_flag", "final_verdict"),
        "price_flag_reason": _get_nested(audit_response, "price_flag", "reason"),
        "retail_classification": retail_response.get("Classification", ""),
        "retail_classi_score": retail_response.get("Classi_Score", ""),
        "retail_confidence": retail_response.get("Confidence", ""),
        "retail_override_applied": retail_response.get("Override_Applied", ""),
        "retail_reason": retail_response.get("Reason", ""),
        "retail_error": retail_response.get("error", ""),
        "price_agent_result": price_response.get("Price_Results", ""),
        "price_agent_score": price_response.get("Price_Score", ""),
        "price_agent_confidence": price_response.get("Confidence", ""),
        "price_value_by_ai": price_response.get("Price_Value_By_AI", ""),
        "price_agent_reason": price_response.get("Price_Reason", ""),
        "price_error": price_response.get("error", ""),
        "buyer_profile_genuineness": buyer_response.get("Genuineness", ""),
        "buyer_profile_score": buyer_response.get("Profile_Score", ""),
        "buyer_profile_confidence": buyer_response.get("Confidence", ""),
        "buyer_profile_reason": buyer_response.get("Profile_Reason", ""),
        "buyer_profile_error": buyer_response.get("error", ""),
    }

    file_exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    return CSV_PATH


def read_audit_dashboard_rows() -> list[Dict[str, str]]:
    _ensure_csv_headers()
    if not os.path.exists(CSV_PATH):
        return []

    with open(CSV_PATH, "r", newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        rows = list(reader)
    rows.reverse()
    return rows
