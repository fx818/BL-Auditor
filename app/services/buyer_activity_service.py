"""Buyer-activity service (ImBuyerActivity/GetData READ API).

Fetches a buyer's recent activity events and normalizes the flat `DETAILS`
list into a timeline structure for the UI.

The fetch follows the same retry/backoff pattern as buylead_service.fetch_buylead_detail.
Parsing is purely deterministic (no LLM).
"""

import asyncio
import logging
import os
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List
from urllib.parse import unquote

import httpx
from dateutil import parser as _dateutil_parser

log = logging.getLogger("bl-auditor.buyer_activity")

# Base URL from env (e.g. http://bizfeed.imutils.com); the GetData path is fixed.
BUYER_ACTIVITY_API_BASE_URL = os.getenv(
    "BUYER_ACTIVITY_API_BASE_URL", "http://bizfeed.imutils.com"
).rstrip("/")
BUYER_ACTIVITY_API_URL = f"{BUYER_ACTIVITY_API_BASE_URL}/ImBuyerActivity/GetData"
TIMEOUT = 60.0
_MAX_RETRIES = int(os.getenv("BUYER_ACTIVITY_MAX_RETRIES", "2"))

# Defaults for the /activity form.
DEFAULT_GLID = "270765971"
# AK is a sensitive JWT — keep it out of source. Set BUYER_ACTIVITY_AK in the env.
DEFAULT_AK = os.getenv("BUYER_ACTIVITY_AK", "")


def default_logtime() -> str:
    """Newer (greater) bound of the default window: now as YYYYMMDDhhmmss."""
    return datetime.now().strftime("%Y%m%d%H%M%S")


def default_endlogtime() -> str:
    """Older bound of the default window: two days ago as YYYYMMDDhhmmss."""
    return (datetime.now() - timedelta(days=2)).strftime("%Y%m%d%H%M%S")


def _offer_instant(data: Dict[str, Any]) -> tuple[datetime, bool] | tuple[None, bool]:
    """Resolve the BL's offer datetime → (datetime, precise).

    Prefers `OFR_DATE` (14-digit YYYYMMDDhhmmss, exact) → precise=True; falls
    back to `ETO_OFR_DATE` (date string, any format via dateutil) → precise=False.
    Returns (None, False) if neither parses.
    """
    raw = str((data or {}).get("OFR_DATE") or "").strip()
    if len(raw) == 14 and raw.isdigit():
        try:
            return datetime.strptime(raw, "%Y%m%d%H%M%S"), True
        except ValueError:
            pass
    od = str((data or {}).get("ETO_OFR_DATE") or "").strip()
    if od:
        try:
            return _dateutil_parser.parse(od), False
        except (ValueError, OverflowError, TypeError):
            pass
    return None, False


def window_from_bl_data(data: Dict[str, Any]) -> tuple[str, str]:
    """(logtime, endlogtime) for a BL: the 2 days before the offer, up to the offer.

    Upper bound (logtime) = the exact offer moment from `OFR_DATE` (so nothing
    after the offer is included); date-only `ETO_OFR_DATE` falls back to that
    day's end. Lower bound (endlogtime) = (offer date − 2d) 00:00:00. Falls back
    to now-2d..now only if no offer date can be parsed.
    """
    inst, precise = _offer_instant(data or {})
    if inst is None:
        log.warning(
            "BL offer date unparsed (OFR_DATE=%r ETO_OFR_DATE=%r) — buyer-activity "
            "window fell back to now-2d..now",
            (data or {}).get("OFR_DATE"), (data or {}).get("ETO_OFR_DATE"),
        )
        return default_logtime(), default_endlogtime()
    logtime = inst.strftime("%Y%m%d%H%M%S") if precise else inst.strftime("%Y%m%d") + "235959"
    endlogtime = (inst - timedelta(days=2)).strftime("%Y%m%d") + "000000"
    return logtime, endlogtime


class BuyerActivityError(RuntimeError):
    """Raised when the activity API is unreachable or returns a failure status."""


async def fetch_buyer_activity(
    glusr_id: str, ak: str, logtime: str, endlogtime: str
) -> Dict[str, Any]:
    """GET buyer activity from ImBuyerActivity/GetData with retry/backoff.

    `logtime` is the newer/greater timestamp bound, `endlogtime` the older one
    (both YYYYMMDDhhmmss). Param names are case-sensitive per the API doc.
    Raises BuyerActivityError on connection/timeout failure after retries, or on HTTP error.
    """
    params = {
        "glusrId": glusr_id,
        "logtime": logtime,
        "endlogtime": endlogtime,
        "AK": ak,
    }
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 2):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(BUYER_ACTIVITY_API_URL, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status != 429 and status < 500:
                # 4xx (not 429): permanent error. Surface body so the caller can
                # show the API's own error message (e.g. 404 IP-mismatch).
                try:
                    return exc.response.json()
                except Exception:
                    raise BuyerActivityError(
                        f"Activity API returned HTTP {status}: {exc.response.text[:500]}"
                    ) from exc
            last_exc = exc
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc

        log.warning(
            "Activity fetch glusr_id=%s attempt %d/%d failed: %s",
            glusr_id, attempt, _MAX_RETRIES + 1, last_exc,
        )
        if attempt <= _MAX_RETRIES:
            await asyncio.sleep(2 ** (attempt - 1))

    raise BuyerActivityError(
        f"Activity fetch failed after {_MAX_RETRIES + 1} attempts "
        f"for glusr_id={glusr_id}: {last_exc}"
    ) from last_exc


def _fmt_datetime(raw: Any) -> str:
    """20260622103407 -> 2026-06-22 10:34:07 (best-effort; returns raw on mismatch)."""
    s = str(raw)
    if len(s) == 14 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}:{s[12:14]}"
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _clean(value: Any) -> Any:
    """Treat the API's placeholder values ('-', 0, None, '') as empty."""
    if value in (None, "-", "[-]", 0, "0", ""):
        return None
    return value


def _decode_kw(value: Any) -> Any:
    """Clean + URL-decode a keyword ('hand%20tool%20kit' -> 'hand tool kit')."""
    v = _clean(value)
    return unquote(v) if isinstance(v, str) else v


def _first_str(value: Any) -> Any:
    """First real entry of a list field (mcat_names / city_names may be '-', [''], ['Durg'])."""
    if isinstance(value, list):
        for v in value:
            if v not in (None, "", "-", 0):
                return v
        return None
    return _clean(value)


def parse_buyer_activity(response: Dict[str, Any], glusr_id: str = "") -> Dict[str, Any]:
    """Flatten the GetData `DETAILS` list into a timeline structure for the template.

    Returns:
        {
          "ok": bool,                     # True only when CODE 200 with data
          "status": str, "code": Any, "message": str, "generated_at": str,
          "error": str | None,            # populated when not ok
          "summary": {...},               # totals incl. BL/ENQ counts
          "type_counts": {activity_type: n},
          "events": [ {datetime, time_label, activity_type, ...}, ... ],  # newest first
        }
    """
    code = response.get("CODE")
    message = response.get("MESSAGE") or response.get("message") or ""
    details = response.get("DETAILS")

    out: Dict[str, Any] = {
        "ok": False,
        "status": "SUCCESS" if code == 200 else "ERROR",
        "code": code,
        "message": message,
        "generated_at": _fmt_datetime(response.get("DATETIME", "")),
        "error": None,
        "summary": {},
        "type_counts": {},
        "events": [],
    }

    # Failure / empty responses (400 missing params, 404 IP mismatch, no data).
    if code != 200 or not isinstance(details, list) or not details:
        parts = [p for p in [message, response.get("unique_id")] if p]
        out["error"] = " | ".join(str(p) for p in parts) or "No activity data returned."
        return out

    events: List[Dict[str, Any]] = []
    type_counts: Counter = Counter()
    cities: Counter = Counter()

    for r in details:
        if not isinstance(r, dict):
            continue
        dt = str(r.get("datetime", ""))
        atype = r.get("activity_type") or "—"
        ev = {
            "datetime": dt,
            "time_label": _fmt_datetime(dt),
            "activity_type": atype,
            "activity_id": r.get("fk_activity_id"),
            "keyword": _decode_kw(r.get("keyword")),
            "mcat_id": _clean(r.get("glcat_mcat_id")),
            "mcat_name": _clean(r.get("glcat_mcat_name")) or _first_str(r.get("mcat_names")),
            "product_disp_id": _clean(r.get("product_disp_id")),
            "seller_glusr_id": _clean(r.get("seller_glusr_id")),
            "city": _first_str(r.get("location_pref_city_names")),
            "request_url": _clean(r.get("request_url")),
        }
        events.append(ev)
        type_counts[atype] += 1
        if ev["city"]:
            cities[ev["city"]] += 1

    # Newest first by default; the UI offers further sorting.
    events.sort(key=lambda e: e["datetime"], reverse=True)
    times = [e["datetime"] for e in events if e["datetime"]]

    out["ok"] = True
    out["events"] = events
    out["type_counts"] = dict(type_counts)
    out["summary"] = {
        "glid": glusr_id or response.get("glusr_id"),
        "total": len(events),
        "bl_count": type_counts.get("BL", 0),
        "enq_count": type_counts.get("ENQ", 0),
        "top_city": cities.most_common(1)[0][0] if cities else None,
        "range_from": _fmt_datetime(min(times)) if times else None,
        "range_to": _fmt_datetime(max(times)) if times else None,
    }
    return out
