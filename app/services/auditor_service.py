from typing import Any, Dict

import httpx

API_URL = "http://3.108.184.25/categorization_outlier"
TIMEOUT = 60.0


async def call_auditor_api(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calls the BL Auditor external API with the given payload.
    Returns the JSON response or raises an exception with the upstream body when available.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(
            API_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text.strip()
            if body:
                raise RuntimeError(f"{exc}. Response body: {body}") from exc
            raise
        return response.json()
