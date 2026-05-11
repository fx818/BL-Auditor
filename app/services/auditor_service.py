import asyncio
import json
from typing import Any, Dict

import requests

API_URL = "http://3.108.184.25/categorization_outlier"

# Using the synchronous `requests` library run in a worker thread instead of
# httpx. Reason: when this call is fired in `asyncio.gather` alongside three
# concurrent HTTPS LLM calls (which use httpx internally via langchain), the
# raw-IP HTTP connect to 3.108.184.25 has been observed to hang under the
# Windows event-loop ↔ httpx interaction. `requests` lives in its own OS
# thread with its own urllib3 connection pool, so it is fully isolated from
# whatever httpx is doing on the asyncio side.
_CONNECT_TIMEOUT = 15.0
_READ_TIMEOUT = 90.0


def _post_blocking(payload: Dict[str, Any]) -> requests.Response:
    return requests.post(
        API_URL,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Connection": "close",
        },
        timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
    )


async def call_auditor_api(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calls the BL Auditor external API with the given payload.

    On failure, raises RuntimeError with an explicit, human-readable message
    that names the failure mode (timeout / HTTP error / connection / bad JSON)
    and includes any response body the upstream returned. Every branch returns
    a non-empty message so the trace UI shows something useful.
    """
    try:
        response = await asyncio.to_thread(_post_blocking, payload)
    except requests.exceptions.ConnectTimeout as exc:
        raise RuntimeError(
            f"Audit API TCP connect timed out after {_CONNECT_TIMEOUT:g}s "
            f"[endpoint {API_URL}] — host did not accept a connection."
        ) from exc
    except requests.exceptions.ReadTimeout as exc:
        raise RuntimeError(
            f"Audit API read timed out after {_READ_TIMEOUT:g}s — upstream "
            f"accepted the connection but did not send a response in time."
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            f"Audit API connection failed: {exc} [endpoint {API_URL}]"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            f"Audit API request error ({exc.__class__.__name__}): {exc}"
        ) from exc

    if not response.ok:
        body = (response.text or "").strip()
        if body:
            raise RuntimeError(
                f"Audit API HTTP {response.status_code}: {body[:1000]}"
            )
        raise RuntimeError(
            f"Audit API HTTP {response.status_code} with empty body."
        )

    try:
        return response.json()
    except json.JSONDecodeError as exc:
        snippet = (response.text or "").strip()[:500]
        raise RuntimeError(
            f"Audit API returned non-JSON (HTTP {response.status_code}): {snippet!r}"
        ) from exc
