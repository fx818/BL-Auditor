# requirements:
# httpx

import asyncio
import base64
import os
import urllib.parse

import httpx

# --- GLID decrypt (RC4, key hardcoded per glid_crypto_service) ---
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


# --- API helpers ---
_TIMEOUT = 60.0


async def _get(url: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def _fetch_all(resolved: str, ak: str, leads_url: str, users_url: str, latest_lead: int) -> dict:
    user_detail, prev_bls, prev_enqs = await asyncio.gather(
        _get(users_url, {
            "token": "imobile@15061981",
            "modid": "Gladmin",
            "AK": ak,
            "glusrid": resolved,
            "others": "ALL",
            "logo": 1,
            "comp_logo": 1,
        }),
        _get(leads_url, {
            "modid": "GLADMIN",
            "latest_lead": latest_lead,
            "glusrid": resolved,
            "token": "imobile1@15061981",
            "AK": ak,
            "type": "B",
        }),
        _get(leads_url, {
            "modid": "GLADMIN",
            "latest_lead": latest_lead,
            "glusrid": resolved,
            "token": "imobile1@15061981",
            "AK": ak,
            "type": "E",
        }),
        return_exceptions=True,
    )

    def _unwrap(r):
        return {"error": str(r)} if isinstance(r, Exception) else r

    return {
        "glid": resolved,
        "user_detail": _unwrap(user_detail),
        "prev_buyleads": _unwrap(prev_bls),
        "prev_enquiries": _unwrap(prev_enqs),
    }


def main(
    encrypted_glid: str,
    latest_lead: int = 10,
) -> dict:
    """Fetch User Detail + last N BuyLeads + last N Enquiries for a buyer.

    Inputs:
      encrypted_glid — FK_GLUSR_USR_ID value from a BuyLead response (base64 RC4)
      latest_lead    — number of BLs / ENQs to fetch (default 10)

    Env vars:
      ACCESS_TOKEN             — shared AK (JWT) for all 3 APIs
      PREV_LEADS_API_BASE_URL  — default http://stg-leads.imutils.com
      USER_DETAIL_API_BASE_URL — default http://stg-users.imutils.com
    """
    try:
        resolved = _decrypt_glid(encrypted_glid.strip())
    except Exception as exc:
        return {"error": f"GLID decrypt failed: {exc}"}

    ak = os.getenv("ACCESS_TOKEN", "")
    leads_base = os.getenv("PREV_LEADS_API_BASE_URL", "http://stg-leads.imutils.com").rstrip("/")
    users_base = os.getenv("USER_DETAIL_API_BASE_URL", "http://stg-users.imutils.com").rstrip("/")

    return asyncio.run(_fetch_all(
        resolved, ak,
        f"{leads_base}/wservce/rfq/display/",
        f"{users_base}/wservce/users/detail/",
        latest_lead,
    ))
