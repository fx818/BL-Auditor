"""Redash-based eligibility filter for BL Auditor consumer.

Checks whether an offer_id is high-sold and non-VAANI before auditing.
Runs synchronously (blocking) — call via asyncio.to_thread() from async code.
"""
import logging
import os
import time

import requests

log = logging.getLogger("bl-auditor.redash_filter")

REDASH_BASE = "https://redash.intermesh.net"
DB_NAME = "pg-imblr-prod-live"

_OFFER_FILTER_SQL = """
SELECT
    o.eto_ofr_display_id AS offer_id
FROM eto_ofr o
JOIN iil_astbuy_intro_count a
    ON o.fk_glcat_mcat_id = a.fk_glcat_mcat_id
WHERE o.eto_ofr_typ = 'B'
  AND o.eto_ofr_approv = 'A'
  AND o.eto_ofr_display_id > 0
  AND o.eto_ofr_display_id = {{offer_id}}
  AND a.tot_supplier = 2
"""

# Cached at first use to avoid a round-trip on every offer.
_data_source_id: int | None = None
_query_id: int | None = int(os.getenv("REDASH_QUERY_ID", 0)) or None


def _resolve_data_source_id(headers: dict) -> int:
    global _data_source_id
    if _data_source_id is not None:
        return _data_source_id
    resp = requests.get(f"{REDASH_BASE}/api/data_sources", headers=headers, timeout=30)
    resp.raise_for_status()
    sources = resp.json()
    ds_id = next((s["id"] for s in sources if s["name"] == DB_NAME), None)
    if ds_id is None:
        available = [s["name"] for s in sources]
        raise ValueError(f"DB '{DB_NAME}' not found in Redash. Available: {available}")
    _data_source_id = ds_id
    return _data_source_id


def _resolve_query_id(headers: dict) -> int:
    global _query_id
    if _query_id is not None:
        return _query_id
    ds_id = _resolve_data_source_id(headers)
    resp = requests.post(
        f"{REDASH_BASE}/api/queries",
        headers=headers,
        json={"name": "bl_auditor_offer_filter", "query": _OFFER_FILTER_SQL, "data_source_id": ds_id, "options": {}},
        timeout=30,
    )
    resp.raise_for_status()
    _query_id = resp.json()["id"]
    log.info("Created Redash query id=%s — set REDASH_QUERY_ID=%s to skip creation on restart", _query_id, _query_id)
    return _query_id


def _run_query(api_key: str, offer_id: str) -> list:
    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json",
    }
    query_id = _resolve_query_id(headers)

    resp = requests.post(
        f"{REDASH_BASE}/api/queries/{query_id}/results",
        headers=headers,
        json={"parameters": {"offer_id": int(offer_id)}},
        timeout=30,
    )
    resp.raise_for_status()
    trigger = resp.json()

    if "query_result" in trigger:
        return trigger["query_result"]["data"]["rows"]

    job_id = trigger["job"]["id"]

    for _ in range(40):
        resp = requests.get(f"{REDASH_BASE}/api/jobs/{job_id}", headers=headers, timeout=30)
        resp.raise_for_status()
        job = resp.json()["job"]
        if job["status"] == 3:
            query_result_id = job["query_result_id"]
            break
        elif job["status"] == 4:
            raise RuntimeError(f"Redash query failed: {job.get('error')}")
        elif job["status"] == 5:
            raise RuntimeError("Redash query was cancelled")
        time.sleep(3)
    else:
        raise TimeoutError("Redash query did not complete within 120s")

    resp = requests.get(
        f"{REDASH_BASE}/api/query_results/{query_result_id}.json",
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["query_result"]["data"]["rows"]


def is_offer_eligible(api_key: str, offer_id: str) -> bool:
    """Return True if offer is high-sold and non-VAANI. Blocking call."""
    rows = _run_query(api_key, offer_id)
    return len(rows) > 0
