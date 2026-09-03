"""Buyer Profile data sources + deterministic profile checks.

Three IndiaMART APIs keyed by the buyer's (decrypted) GLID, all authenticated
with a single shared AK from env (``ACCESS_TOKEN``):

1. Prev BuyLeads  — ``<leads>/wservce/rfq/display/?type=B``
2. Prev Enquiries — ``<leads>/wservce/rfq/display/?type=E`` (same endpoint)
3. User Detail    — ``<users>/wservce/users/detail/``

The extract/evaluate helpers are purely deterministic (no LLM): they shape the
raw responses and compute profile completeness + tenure.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

import httpx

from app.services.glid_crypto_service import decrypt_glid

log = logging.getLogger("bl-auditor.buyer_profile_service")

LEADS_API_BASE_URL = os.getenv("PREV_LEADS_API_BASE_URL", "http://leads.imutils.com").rstrip("/")
USERS_API_BASE_URL = os.getenv("USER_DETAIL_API_BASE_URL", "http://users.imutils.com").rstrip("/")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")

LEADS_API_URL = f"{LEADS_API_BASE_URL}/wservce/rfq/display/"
USERS_API_URL = f"{USERS_API_BASE_URL}/wservce/users/detail/"

TIMEOUT = 60.0
_MAX_RETRIES = int(os.getenv("BUYER_PROFILE_MAX_RETRIES", "2"))

# Fixed query constants (differ per service — observed in sample responses).
_LEADS_TOKEN = "imobile1@15061981"
_LEADS_MODID = "GLADMIN"
_USERS_TOKEN = "imobile@15061981"
_USERS_MODID = "Gladmin"

# Tenure threshold: a member for fewer than this many days is "New".
_NEW_MEMBER_DAYS = 183  # ~6 months

# Spam-name heuristic: a real person's name (firstname + lastname) rarely exceeds
# this many words; more than this flags the profile Incomplete.
_MAX_NAME_WORDS = 4


async def _get_json(url: str, params: Dict[str, Any], *, what: str) -> Dict[str, Any]:
    """GET with the shared httpx retry/backoff convention (see buylead_service)."""
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 2):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status != 429 and status < 500:
                raise  # 4xx (not 429): permanent error, no retry
            last_exc = exc
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
        # Any other exception propagates immediately

        log.warning("%s fetch attempt %d/%d failed: %s", what, attempt, _MAX_RETRIES + 1, last_exc)
        if attempt <= _MAX_RETRIES:
            await asyncio.sleep(2 ** (attempt - 1))

    raise RuntimeError(f"{what} fetch failed after {_MAX_RETRIES + 1} attempts: {last_exc}") from last_exc


async def _fetch_rfq_display(glid: str, *, offer_type: str, latest_lead: int, what: str) -> Dict[str, Any]:
    params = {
        "modid": _LEADS_MODID,
        "latest_lead": latest_lead,
        "glusrid": glid,
        "token": _LEADS_TOKEN,
        "AK": ACCESS_TOKEN,
        "type": offer_type,
    }
    return await _get_json(LEADS_API_URL, params, what=what)


async def fetch_prev_buyleads(glid: str, latest_lead: int = 10) -> Dict[str, Any]:
    """The buyer's previous BuyLeads (type=B)."""
    return await _fetch_rfq_display(glid, offer_type="B", latest_lead=latest_lead, what="PrevBuyLeads")


async def fetch_prev_enquiries(glid: str, latest_lead: int = 10) -> Dict[str, Any]:
    """The buyer's previous enquiries (type=E)."""
    return await _fetch_rfq_display(glid, offer_type="E", latest_lead=latest_lead, what="PrevEnquiries")


async def fetch_user_detail(glid: str) -> Dict[str, Any]:
    """The buyer's master user profile."""
    params = {
        "token": _USERS_TOKEN,
        "modid": _USERS_MODID,
        "AK": ACCESS_TOKEN,
        "glusrid": glid,
        "others": "ALL",
        "logo": 1,
        "comp_logo": 1,
    }
    return await _get_json(USERS_API_URL, params, what="UserDetail")


async def fetch_user_detail_for_buylead(buylead_response: Dict[str, Any]) -> Dict[str, Any]:
    """Decrypt the buyer GLID from a BuyLead response and fetch the User Detail API.

    Never raises: returns ``{}`` when the GLID is missing/undecryptable or the
    fetch fails. Used by the audit orchestration to fetch User Detail once and
    pass it to both the ISQ and buyer-profile agents (avoids a duplicate call).
    """
    data = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {}) or {}
    enc_glid = data.get("FK_GLUSR_USR_ID")
    if not enc_glid:
        return {}
    try:
        glid = decrypt_glid(str(enc_glid))
    except Exception as exc:
        log.warning("fetch_user_detail_for_buylead: GLID decrypt failed: %s", exc)
        return {}
    try:
        return await fetch_user_detail(glid)
    except Exception as exc:
        log.warning("fetch_user_detail_for_buylead: user detail fetch failed: %s", exc)
        return {}


def _listing(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    listing = (resp or {}).get("RESPONSE", {}).get("DATA", {}).get("Listing") or []
    return [row for row in listing if isinstance(row, dict)]


def extract_prev_bls(resp: Dict[str, Any], limit: int = 10) -> List[Dict[str, str]]:
    """[{title, desc}] from a prev-BuyLeads response (title/desc, max ``limit``)."""
    out: List[Dict[str, str]] = []
    for row in _listing(resp)[:limit]:
        title = str(row.get("ETO_OFR_TITLE") or "").strip()
        desc = str(row.get("ETO_OFR_DESC") or "").strip()
        if title or desc:
            out.append({"title": title, "desc": desc})
    return out


def extract_prev_enqs(resp: Dict[str, Any], limit: int = 10) -> List[Dict[str, str]]:
    """[{subject, message}] from a prev-enquiries response (max ``limit``)."""
    out: List[Dict[str, str]] = []
    for row in _listing(resp)[:limit]:
        subject = str(row.get("SUBJECT") or "").strip()
        message = str(row.get("MESSAGE") or "").strip()
        if subject or message:
            out.append({"subject": subject, "message": message})
    return out


def extract_user_profile(resp: Dict[str, Any]) -> Dict[str, str]:
    """Flatten the User Detail response to the fields the profile checks need."""
    d = resp or {}
    return {
        "firstname": str(d.get("glusr_usr_firstname") or "").strip(),
        "lastname": str(d.get("glusr_usr_lastname") or "").strip(),
        "companyname": str(d.get("glusr_usr_companyname") or "").strip(),
        "email": str(d.get("glusr_usr_email") or "").strip(),
        "mobile": str(d.get("glusr_usr_ph_mobile") or "").strip(),
        "country": str(d.get("country_name") or "").strip(),
        "membersince": str(d.get("glusr_usr_membersince") or "").strip(),
    }


def evaluate_profile_completeness(profile: Dict[str, str]) -> Dict[str, str]:
    """Deterministic completeness check.

    - name_ok       : firstname OR companyname present (either suffices)
    - name_too_long : firstname + lastname has more than ``_MAX_NAME_WORDS`` words
                      (spam-name heuristic) -> Incomplete
    - contact_ok    : India -> mobile present; otherwise -> email present
    - status        : Complete iff name_ok AND not name_too_long AND contact_ok
    """
    firstname = profile.get("firstname", "")
    lastname = profile.get("lastname", "")
    name_ok = bool(firstname) or bool(profile.get("companyname"))

    name_word_count = len(f"{firstname} {lastname}".split())
    name_too_long = name_word_count > _MAX_NAME_WORDS

    is_india = profile.get("country", "").strip().lower() == "india"
    if is_india:
        contact_ok = bool(profile.get("mobile"))
        contact_label = "mobile"
    else:
        contact_ok = bool(profile.get("email"))
        contact_label = "email"

    issues: List[str] = []
    if not name_ok:
        issues.append("name/company missing")
    if name_too_long:
        issues.append(f"name has {name_word_count} words (>{_MAX_NAME_WORDS})")
    if not contact_ok:
        issues.append(f"{contact_label} missing")

    if not issues:
        reason = "Name/company present, name length ok, and required contact present."
    else:
        reason = "Incomplete: " + "; ".join(issues) + "."

    return {"status": "Complete" if not issues else "Incomplete", "reason": reason}


def evaluate_tenure(membersince: Any) -> str:
    """``New`` if member for < ~6 months, ``Old`` otherwise, ``Unknown`` if unparseable.

    ``membersince`` is ``YYYYMMDDHHMMSS`` (e.g. ``20200606140933``).
    """
    raw = str(membersince or "").strip()
    if len(raw) < 8 or not raw[:8].isdigit():
        return "Unknown"
    try:
        joined = datetime.strptime(raw[:8], "%Y%m%d")
    except ValueError:
        return "Unknown"
    days = (datetime.now() - joined).days
    return "New" if days < _NEW_MEMBER_DAYS else "Old"
