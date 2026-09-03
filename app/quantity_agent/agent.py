"""Quantity Audit agent — deterministic, no LLM.

Flags a BuyLead's quantity ISQ value as ABSURD when it trips any of these rules:

  R1 price_equals_qty  : qty > 1000 AND qty equals a viewed (enquired) product's price.
  R2 qty_in_price_band : qty > 1000 AND qty falls inside the MCAT's [q1, q3] price band.
  R3 too_many_digits   : qty has 6 or more digits (>= 100000).
  R4 sequence_or_repeat: qty digits form an ascending/descending consecutive run
                         (123, 12345, 54321) OR the leading digit repeats >= 3 times
                         (111, 11100, 222000).
  R5 heavy_unit        : qty unit is a bulk B2B-implausible family (tonne/quintal/
                         truckload/container/wagon) AND qty exceeds that family's
                         per-family threshold.
  R6 qty_equals_contact: qty digits EXACTLY equal the buyer's own mobile number
                         (glusr_usr_ph_mobile) or PIN code (glusr_usr_zip), from the
                         User Detail API. Mirrors the ISQ agent's hard check. (Note:
                         such a qty also trips R3 too_many_digits — both are listed.)

All checks are pure functions of the BuyLead response — qty/unit/MCAT band and the
enquired-product prices are reused from price_agent's parsing helpers so the two
agents stay in sync.

Reasons for every rule that fired are listed; status is "Absurd" if any fired,
"Unverifiable" if the quantity could not be parsed, else "OK".
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

from app.price_agent.agent import (
    _bl_detail_and_keys,
    _build_offer_source,
    _canonical_unit,
    _extract_qty,
    _find_price_data,
    _normalize_mcat_unit,
)

log = logging.getLogger("bl-auditor.quantity_agent")

# Rules 1 & 2 only apply to genuinely large quantities.
_LARGE_QTY_THRESHOLD = 1000

# "6 or more digits" → qty >= 100000.
_MAX_DIGITS = 6

# Heavy / bulk unit families implausible for a typical B2B BuyLead above a
# per-family quantity threshold. Match keys are space-stripped uppercase forms
# (e.g. "metric ton" → "METRICTON"). Tweak thresholds here if business says so.
_HEAVY_UNIT_FAMILIES = [
    ("tonne", 100, {
        "TON", "TONS", "TONN", "TONNS", "TONNE", "TONNES", "TONES", "TONE",
        "MT", "METRICTON", "METRICTONS", "METRICTONNE", "METRICTONNES",
    }),
    ("quintal", 100, {"QUINTAL", "QUINTALS", "QTL", "QTLS"}),
    ("truckload", 50, {"TRUCK", "TRUCKS", "TRUCKLOAD", "TRUCKLOADS"}),
    ("container", 50, {"CONTAINER", "CONTAINERS"}),
    ("wagon", 50, {"WAGON", "WAGONS"}),
]


def _fail(reason: str, **extra: Any) -> Dict[str, Any]:
    return {
        "status": "Unverifiable",
        "reason": reason,
        "rules_fired": [],
        "qty_value": None,
        "qty_raw": "",
        "qty_unit": "",
        **extra,
    }


def _parse_price(value: Any) -> Optional[float]:
    """Parse an enquired-product price string ("₹1,000", "1000") into a float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^\d.]", "", str(value))
    if not s or s == ".":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _is_sequence_or_repeat(qty_int: int) -> Optional[str]:
    """Return a human reason if the digit string is a consecutive run or has a
    leading digit repeated >= 3 times; else None."""
    s = str(qty_int)
    if len(s) < 3:
        return None
    digits = [int(c) for c in s]

    # Leading digit repeated >= 3 times (111, 11100, 222000).
    lead = 1
    for d in digits[1:]:
        if d == digits[0]:
            lead += 1
        else:
            break
    if lead >= 3:
        return f"leading digit '{s[0]}' repeats {lead} times"

    # Whole string is an ascending consecutive run (123, 12345).
    if all(digits[i] - digits[i - 1] == 1 for i in range(1, len(digits))):
        return "digits ascend consecutively"

    # Whole string is a descending consecutive run (54321).
    if all(digits[i - 1] - digits[i] == 1 for i in range(1, len(digits))):
        return "digits descend consecutively"

    return None


def _heavy_unit_hit(unit_key: str, qty_value: float) -> Optional[str]:
    for label, threshold, members in _HEAVY_UNIT_FAMILIES:
        if unit_key in members and qty_value > threshold:
            return f"{label} unit with qty {int(qty_value)} exceeds B2B threshold {threshold}"
    return None


def _digits_only(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _contact_match(qty_raw: str, user_detail: Dict[str, Any]) -> Optional[str]:
    """R6: qty digits EXACTLY equal the buyer's own mobile number or PIN code.

    Catches buyers who paste their phone/pincode into the quantity field. Returns
    a human reason on a hit, else None. Mirrors the ISQ agent's hard check.
    """
    qty_digits = _digits_only(qty_raw)
    if not qty_digits:
        return None
    mobile = _digits_only((user_detail or {}).get("glusr_usr_ph_mobile"))
    zip_code = _digits_only((user_detail or {}).get("glusr_usr_zip"))
    if mobile and qty_digits == mobile:
        return f"qty ({qty_digits}) exactly matches the buyer's own mobile number"
    if zip_code and qty_digits == zip_code:
        return f"qty ({qty_digits}) exactly matches the buyer's own PIN code"
    return None


def run_quantity_agent(
    offer_id: str,
    buylead_response: Dict[str, Any],
    user_detail: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Deterministic quantity audit. Returns a dict with status/reason/rules_fired.

    ``user_detail`` is the buyer's User Detail API response (fetched once by the
    audit orchestration); when provided, R6 checks qty against mobile/zip.
    """
    started = time.perf_counter()
    try:
        source = _build_offer_source(offer_id, buylead_response or {})
        offer = _bl_detail_and_keys(source)
    except Exception as exc:  # parsing should never be fatal
        log.warning("quantity_agent parse failed: %s", exc)
        return _fail(f"could not parse BuyLead for quantity audit: {exc}",
                     duration_ms=int((time.perf_counter() - started) * 1000))

    qty_raw = str(offer.get("Qty") or "").strip()
    qty_value = _extract_qty(qty_raw)
    unit_display = _canonical_unit(qty_raw)
    unit_key = unit_display.replace(" ", "")

    if qty_value is None:
        return _fail("quantity missing or not numeric",
                     qty_raw=qty_raw, qty_unit=unit_display,
                     duration_ms=int((time.perf_counter() - started) * 1000))

    reasons: List[str] = []
    rules: List[str] = []
    qty_int = int(qty_value)

    # R1 — qty equals a viewed (enquired) product's price (only for large qty).
    if qty_value > _LARGE_QTY_THRESHOLD:
        for card in offer.get("BL_card") or []:
            price = _parse_price(card.get("Price"))
            if price is not None and price == qty_value:
                rules.append("price_equals_qty")
                reasons.append(
                    f"qty ({qty_int}) equals a viewed product's price "
                    f"({card.get('Item Name') or 'product'}: {card.get('Price')})"
                )
                break

    # R2 — qty sits inside the MCAT's [q1, q3] price band (only for large qty).
    if qty_value > _LARGE_QTY_THRESHOLD:
        band = _find_price_data(_normalize_mcat_unit(offer.get("MCAT_Unit")))
        if band:
            q1 = band.get("q1") or 0
            q3 = band.get("q3") or 0
            if q1 and q3 and q1 <= qty_value <= q3:
                rules.append("qty_in_price_band")
                reasons.append(
                    f"qty ({qty_int}) falls inside the MCAT price band "
                    f"[{int(q1)}, {int(q3)}]"
                )

    # R3 — too many digits (>= 6).
    if len(str(qty_int)) > _MAX_DIGITS:
        rules.append("too_many_digits")
        reasons.append(f"qty ({qty_int}) has {len(str(qty_int))} digits (>{_MAX_DIGITS})")

    # R4 — sequence / leading-digit repeat.
    seq_reason = _is_sequence_or_repeat(qty_int)
    if seq_reason:
        rules.append("sequence_or_repeat")
        reasons.append(f"qty ({qty_int}) looks like a placeholder: {seq_reason}")

    # R5 — heavy / bulk unit above its per-family threshold.
    heavy_reason = _heavy_unit_hit(unit_key, qty_value)
    if heavy_reason:
        rules.append("heavy_unit")
        reasons.append(heavy_reason)

    # R6 — qty equals the buyer's own mobile number or PIN code.
    contact_reason = _contact_match(qty_raw, user_detail or {})
    if contact_reason:
        rules.append("qty_equals_contact")
        reasons.append(contact_reason)

    status = "Absurd" if rules else "OK"
    reason = "; ".join(reasons) if reasons else "quantity within plausible B2B bounds"

    return {
        "status": status,
        "reason": reason,
        "rules_fired": rules,
        "qty_value": qty_value,
        "qty_raw": qty_raw,
        "qty_unit": unit_display,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
