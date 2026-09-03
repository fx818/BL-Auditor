# requirements:
# httpx

import os
import time

import httpx


def main(offer_id: str) -> dict:
    """Windmill step 1: fetch raw BuyLead response from the BuyLead API."""
    base_url = (
        os.environ.get(
            "BUYLEAD_API_URL",
            "http://dev-leads.imutils.com/wservce/buyleads/detail/",
        ).rstrip("/")
        + "/"
    )
    token = os.environ.get("BUYLEAD_API_KEY", "imobile@15061981")

    params = {
        "modid": "ETO",
        "offer_type": "B",
        "buyer_response": "2",
        "additionalinfo_format": "JSON",
        "token": token,
        "breadcrumb": "1",
        "offer": offer_id,
    }

    last_exc = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.get(base_url, params=params)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500 and exc.response.status_code != 429:
                raise
            last_exc = exc
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc

        if attempt < 2:
            time.sleep(2**attempt)

    raise RuntimeError(
        f"BuyLead fetch failed after 3 attempts for offer_id={offer_id}: {last_exc}"
    ) from last_exc
