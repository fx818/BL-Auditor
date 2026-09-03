# requirements:
# httpx
# python-dateutil

import asyncio
import base64
import os
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import unquote

import httpx
from dateutil import parser as _dateutil_parser

# RC4 key shared with glid_crypto_service
_RC4_KEY = "1996c39iil"


def _rc4(data: bytes, key: str) -> bytes:
    s = list(range(256))
    kb = key.encode()
    j = 0
    for i in range(256):
        j = (j + s[i] + kb[i % len(kb)]) % 256
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray()
    for byte in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        k = s[(s[i] + s[j]) % 256]
        out.append(byte ^ k)
    return bytes(out)


def _decrypt_glid(enc: str) -> str:
    return _rc4(base64.b64decode(urllib.parse.unquote(enc)), _RC4_KEY).decode()


def _fmt_dt(raw: str) -> str:
    s = str(raw)
    if len(s) == 14 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}:{s[12:14]}"
    return s


def _parse_activity(response: dict, glid: str) -> dict:
    code = response.get("CODE")
    message = response.get("MESSAGE") or response.get("message") or ""
    details = response.get("DETAILS")

    out = {
        "ok": False,
        "code": code,
        "message": message,
        "error": None,
        "events": [],
        "type_counts": {},
        "summary": {},
    }

    if code != 200 or not isinstance(details, list) or not details:
        out["error"] = message or "No activity data returned."
        return out

    events, type_counts, cities = [], Counter(), Counter()
    for r in details:
        if not isinstance(r, dict):
            continue
        dt = str(r.get("datetime", ""))
        atype = r.get("activity_type") or "—"
        kw = r.get("keyword")
        if kw not in (None, "-", ""):
            kw = unquote(str(kw))
        else:
            kw = None
        city_raw = r.get("location_pref_city_names")
        city = (city_raw[0] if isinstance(city_raw, list) and city_raw else
                str(city_raw).strip() if city_raw and city_raw not in ("-", "") else None)
        events.append({
            "datetime": dt,
            "time_label": _fmt_dt(dt),
            "activity_type": atype,
            "activity_id": r.get("fk_activity_id"),
            "keyword": kw,
            "city": city,
        })
        type_counts[atype] += 1
        if city:
            cities[city] += 1

    events.sort(key=lambda e: e["datetime"], reverse=True)
    times = [e["datetime"] for e in events if e["datetime"]]

    out.update({
        "ok": True,
        "events": events,
        "type_counts": dict(type_counts),
        "summary": {
            "glid": glid,
            "total": len(events),
            "bl_count": type_counts.get("BL", 0),
            "enq_count": type_counts.get("ENQ", 0),
            "top_city": cities.most_common(1)[0][0] if cities else None,
            "range_from": _fmt_dt(min(times)) if times else None,
            "range_to": _fmt_dt(max(times)) if times else None,
        },
    })
    return out


async def _fetch(url: str, params: dict, max_retries: int) -> dict:
    last_exc = None
    for attempt in range(1, max_retries + 2):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            last_exc = exc
            if attempt <= max_retries:
                await asyncio.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"Activity fetch failed after {max_retries + 1} attempts: {last_exc}") from last_exc


def main(
    encrypted_glid: str,
    logtime: str = "",
    endlogtime: str = "",
) -> dict:
    """Fetch buyer activity from ImBuyerActivity/GetData.

    Inputs:
      encrypted_glid — FK_GLUSR_USR_ID value from a BuyLead response (base64 RC4)
      logtime        — YYYYMMDDHHMMSS upper bound (default: now)
      endlogtime     — YYYYMMDDHHMMSS lower bound (default: 2 days before logtime)

    Env vars:
      BUYER_ACTIVITY_AK           — JWT AK for the API (required)
      BUYER_ACTIVITY_API_BASE_URL — default http://bizfeed.imutils.com
      BUYER_ACTIVITY_MAX_RETRIES  — default 2
    """
    try:
        glid = _decrypt_glid(encrypted_glid.strip())
    except Exception as exc:
        return {"ok": False, "error": f"GLID decrypt failed: {exc}"}

    now = datetime.now()
    lt = logtime.strip() if logtime.strip() else now.strftime("%Y%m%d%H%M%S")
    if endlogtime.strip():
        elt = endlogtime.strip()
    else:
        try:
            ref = _dateutil_parser.parse(lt) if not lt.isdigit() else datetime.strptime(lt, "%Y%m%d%H%M%S")
        except Exception:
            ref = now
        elt = (ref - timedelta(days=2)).strftime("%Y%m%d") + "000000"

    ak = os.getenv("BUYER_ACTIVITY_AK", "")
    base_url = os.getenv("BUYER_ACTIVITY_API_BASE_URL", "http://bizfeed.imutils.com").rstrip("/")
    max_retries = int(os.getenv("BUYER_ACTIVITY_MAX_RETRIES", "2"))

    url = f"{base_url}/ImBuyerActivity/GetData"
    params = {"glusrId": glid, "logtime": lt, "endlogtime": elt, "AK": ak}

    try:
        raw = asyncio.run(_fetch(url, params, max_retries))
    except Exception as exc:
        return {"ok": False, "glid": glid, "error": str(exc)}

    result = _parse_activity(raw, glid)
    result["_params"] = {"logtime": lt, "endlogtime": elt}
    return result
