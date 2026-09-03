# requirements:
# httpx
# python-dateutil

import asyncio
import base64
import json
import re
import os
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import unquote

import httpx
from dateutil import parser as _dateutil_parser

_RC4_KEY = "1996c39iil"
_ACTIVITY_KEYWORD_IDS = {1282, 559, 548, 4231}
_TERM_STOPWORDS = {"a", "an", "the", "and", "or", "for", "of", "to", "with", "in"}


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


def _window(ofr_date: str = "", eto_ofr_date: str = "") -> tuple:
    """
    Compute (logtime, endlogtime) for the buyer-activity lookup window.

    ofr_date: expected as a 14-digit YYYYMMDDHHMMSS string (precise timestamp).
    eto_ofr_date: fallback, any date string dateutil can parse.
    If neither is usable, falls back to "now".
    """
    raw = str(ofr_date or "").strip()
    inst, precise = None, False
    if len(raw) == 14 and raw.isdigit():
        try:
            inst, precise = datetime.strptime(raw, "%Y%m%d%H%M%S"), True
        except ValueError:
            pass
    if inst is None:
        od = str(eto_ofr_date or "").strip()
        if od:
            try:
                inst = _dateutil_parser.parse(od)
            except Exception:
                pass
    if inst is None:
        now = datetime.now()
        return now.strftime("%Y%m%d%H%M%S"), (now - timedelta(days=2)).strftime("%Y%m%d%H%M%S")
    logtime = inst.strftime("%Y%m%d%H%M%S") if precise else inst.strftime("%Y%m%d") + "235959"
    endlogtime = (inst - timedelta(days=2)).strftime("%Y%m%d") + "000000"
    return logtime, endlogtime


def _clean(v):
    return None if v in (None, "-", "[-]", 0, "0", "") else v


def _first_str(v):
    if isinstance(v, list):
        for x in v:
            if x not in (None, "", "-", 0):
                return x
        return None
    return _clean(v)


def _fmt_dt(raw):
    s = str(raw)
    if len(s) == 14 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}:{s[12:14]}"
    return s


def _parse_activity(response: dict, glusr_id: str = "") -> dict:
    code = response.get("CODE")
    message = response.get("MESSAGE") or response.get("message") or ""
    details = response.get("DETAILS")
    out = {
        "ok": False, "status": "SUCCESS" if code == 200 else "ERROR",
        "code": code, "message": message, "error": None, "events": [],
        "type_counts": {}, "summary": {},
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
        kw = _clean(r.get("keyword"))
        if isinstance(kw, str):
            kw = unquote(kw)
        events.append({
            "datetime": dt, "time_label": _fmt_dt(dt),
            "activity_type": atype, "activity_id": r.get("fk_activity_id"),
            "keyword": kw,
        })
        type_counts[atype] += 1
        city = _first_str(r.get("location_pref_city_names"))
        if city and isinstance(city, str):
            cities[city] += 1
    events.sort(key=lambda e: e["datetime"], reverse=True)
    times = [e["datetime"] for e in events if e["datetime"]]
    out.update({
        "ok": True, "events": events, "type_counts": dict(type_counts),
        "summary": {
            "glid": glusr_id, "total": len(events),
            "bl_count": type_counts.get("BL", 0), "enq_count": type_counts.get("ENQ", 0),
            "top_city": cities.most_common(1)[0][0] if cities else None,
            "range_from": _fmt_dt(min(times)) if times else None,
            "range_to": _fmt_dt(max(times)) if times else None,
        }
    })
    return out


def _collect_keywords(buyer_activity: dict) -> list:
    seen, out = set(), []
    for ev in (buyer_activity or {}).get("events") or []:
        if ev.get("activity_id") in _ACTIVITY_KEYWORD_IDS:
            kw = str(ev.get("keyword") or "").strip()
            if kw and kw.lower() not in seen:
                seen.add(kw.lower())
                out.append(kw)
    return out


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower().replace("-", " "))
            if t not in _TERM_STOPWORDS}


def _match_terms(desc: str, buyer_activity: dict) -> dict:
    text = (desc or "").strip()
    prefix = "buyer searched for"
    if text.lower().startswith(prefix):
        text = text[len(prefix):]
    terms = []
    for chunk in text.split(","):
        term = re.sub(r"^\s*(?:and|or)\s+", "", chunk.strip(), flags=re.IGNORECASE).strip()
        if term:
            terms.append(term)
    log_kws = _collect_keywords(buyer_activity)
    kw_tok = [(kw, _tokens(kw)) for kw in log_kws]
    results = []
    for term in terms:
        t_tok = _tokens(term)
        matched = None
        # Guard: if t_tok is empty (all stopwords / punctuation), never match anything.
        # Without this, {} <= k_tok is True for any non-empty keyword set.
        if t_tok:
            for kw, k_tok in kw_tok:
                if k_tok and (k_tok <= t_tok or t_tok <= k_tok):
                    matched = kw
                    break
        results.append({"term": term, "found": matched is not None, "matched_keyword": matched})
    return {
        "terms": results, "log_keywords": log_kws,
        "found_count": sum(1 for r in results if r["found"]), "total": len(terms),
    }


async def _run(
    enc_glid: str,
    mcat_name: str,
    description: str,
    desc_audit_raw: dict,
    ofr_date: str = "",
    eto_ofr_date: str = "",
    title: str = "",
) -> dict:
    mcat_name = str(mcat_name or "").strip() or "Unknown"
    desc_text = str(description or "").strip()

    # desc_audit_raw is expected to be the output of normalize_description, which
    # always returns a plain dict with a guaranteed "status" key.
    desc_status = (desc_audit_raw or {}).get("status", "") if isinstance(desc_audit_raw, dict) else ""

    enc_glid = str(enc_glid or "").strip()

    _base = {
        "buyer_glid": None, "buyer_activity": None, "term_match": None,
        "candidates": [], "candidates_str": "[]", "needs_keyword_selection": False,
        "mcat_name": mcat_name, "title": title,
    }

    if not enc_glid:
        return {**_base, "error": "No encrypted GLID provided"}

    try:
        buyer_glid = _decrypt_glid(enc_glid)
    except Exception as exc:
        return {**_base, "error": f"GLID decrypt failed: {exc}"}

    logtime, endlogtime = _window(ofr_date, eto_ofr_date)
    base_url = "http://10.142.0.9"
    api_url = f"{base_url}/ImBuyerActivity/GetData"
    ak = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjIzMDAiLCJleHAiOjE3ODQxNzk0MTUsImlhdCI6MTc4NDA5MzAxNSwiaXNzIjoiRU1QTE9ZRUUifQ.nORDOfYiKAQdyCT7P3nr53Cb45VUAo0sWsh9MiR2eS4"
    max_retries = int(os.getenv("BUYER_ACTIVITY_MAX_RETRIES", "2"))

    ba_raw, last_exc = None, None
    for attempt in range(1, max_retries + 2):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(api_url, params={
                    "glusrId": buyer_glid, "logtime": logtime,
                    "endlogtime": endlogtime, "AK": ak,
                })
                resp.raise_for_status()
                ba_raw = resp.json()
                break
        except Exception as exc:
            last_exc = exc
            if attempt <= max_retries:
                await asyncio.sleep(2 ** (attempt - 1))

    if ba_raw is None:
        return {**_base, "buyer_glid": buyer_glid,
                "error": f"Activity fetch failed: {last_exc}"}

    buyer_activity = _parse_activity(ba_raw, glusr_id=buyer_glid)
    candidates, term_match, needs_kw = [], None, False

    # Always collect candidates and run term_match for "buyer searched for" descriptions
    # so downstream steps have full observability. Only flag for keyword replacement
    # when the description audit explicitly says Incorrect.
    if desc_text.lower().startswith("buyer searched for") and buyer_activity.get("ok"):
        candidates = _collect_keywords(buyer_activity)
        term_match = _match_terms(desc_text, buyer_activity)
        needs_kw = bool(candidates) and desc_status == "Incorrect"

    return {
        "buyer_glid": buyer_glid,
        "buyer_activity": buyer_activity,
        "term_match": term_match,
        "candidates": candidates,
        "candidates_str": json.dumps(candidates, ensure_ascii=False),
        "needs_keyword_selection": needs_kw,
        "mcat_name": mcat_name,
        "title": title,
        "error": None,
    }


def main(
    enc_glid: str,
    mcat_name: str,
    description: str,
    desc_audit_raw: dict,
    ofr_date: str = "",
    eto_ofr_date: str = "",
    title: str = "",
) -> dict:
    """
    Direct-input version -- no buylead_response/offer objects required.

    Args:
        enc_glid: encrypted GLID string (e.g. FK_GLUSR_USR_ID from a BuyLead).
        mcat_name: category name for the offer.
        description: the offer/buylead description text (used to detect the
            "buyer searched for ..." pattern and drive keyword matching).
        desc_audit_raw: output of the description-normalization step, a dict
            with at least a "status" key (e.g. {"status": "Incorrect"}).
        ofr_date: optional 14-digit YYYYMMDDHHMMSS timestamp used to build the
            activity lookup window. Falls back to eto_ofr_date, then now().
        eto_ofr_date: optional fallback date string (any format dateutil can
            parse) if ofr_date isn't available/precise.
        title: optional offer title, passed through in the output for
            downstream steps (not currently used in any matching logic here).
    """
    return asyncio.run(_run(
        enc_glid=enc_glid,
        mcat_name=mcat_name,
        description=description,
        desc_audit_raw=desc_audit_raw,
        ofr_date=ofr_date,
        eto_ofr_date=eto_ofr_date,
        title=title,
    ))
