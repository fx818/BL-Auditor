import asyncio
import logging
import os
from typing import Any, Dict

import httpx

log = logging.getLogger("bl-auditor.windmill")

WINDMILL_BASE_URL = os.getenv("WINDMILL_BASE_URL", "https://windmill.intermesh.net/api/w/indiamart-workspace")
_FLOW_RUN_BASE = f"{WINDMILL_BASE_URL}/jobs/run_wait_result/f"

_CONNECT_TIMEOUT = 15.0
_READ_TIMEOUT = 90.0

_OUTLIER_MAP = {
    "outlier": "Wrong",
    "not_outlier": "Correct",
    "not-outlier": "Correct",
    "not outlier": "Correct",
}


def _normalize_status(status: Any) -> str:
    if not isinstance(status, str):
        return status or ""
    mapped = _OUTLIER_MAP.get(status.strip().lower())
    return mapped if mapped is not None else status


async def _call_flow(client: httpx.AsyncClient, flow_path: str, body: Dict) -> Dict:
    token = os.getenv("WINDMILL_KEY", "")
    url = f"{_FLOW_RUN_BASE}/{flow_path}"
    resp = await client.post(
        url,
        json=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
    )
    resp.raise_for_status()
    return resp.json()


def _extract_verdict(raw: Dict) -> Dict[str, str]:
    resp = (raw or {}).get("response") or {}
    return {
        "status": _normalize_status(resp.get("status", "")),
        "reason": resp.get("reason", ""),
    }


async def call_windmill_agents(payload: Dict[str, Any]) -> Dict[str, Any]:
    item_name = payload.get("item_name", "")
    mcat_name = payload.get("mcat_name", "")
    specs = payload.get("ISQ", [])
    pc_item_id = str(payload.get("pc_item_id", "") or "")
    api_key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("WINDMILL_LLM_MODEL") or os.getenv("LLM_MODEL", "google/gemini-2.5-flash-lite")

    async with httpx.AsyncClient() as client:
        try:
            title_cat_raw, title_spec_raw, specs_cat_raw = await asyncio.gather(
                _call_flow(client, "f/small_agents/title_category_mismatch_agent_flow", {
                    "item_name": item_name,
                    "mcat_name": mcat_name,
                    "api_key": api_key,
                    "model": model,
                }),
                _call_flow(client, "f/small_agents/title_specs_mismatch_agent_flow", {
                    "item_name": item_name,
                    "specs": specs,
                    "api_key": api_key,
                    "model": model,
                }),
                _call_flow(client, "f/small_agents/specs_category_mismatch_agent_flow", {
                    "specs": specs,
                    "mcat_name": mcat_name,
                    "pc_item_id": pc_item_id,
                    "api_key": api_key,
                    "model": model,
                }),
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"Windmill agent timed out: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            try:
                msg = exc.response.json().get("error", {}).get("message", exc.response.text[:200])
            except Exception:
                msg = exc.response.text[:200]
            raise RuntimeError(f"Windmill {exc.response.status_code}: {msg}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"Windmill agent request error: {exc}") from exc

    return {
        "title_category_outlier": _extract_verdict(title_cat_raw),
        "title_spec_verdict":     _extract_verdict(title_spec_raw),
        "specs_category_outlier": _extract_verdict(specs_cat_raw),
    }
