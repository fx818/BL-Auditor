import os
import httpx

WINDMILL_BASE_URL = os.getenv("WINDMILL_BASE_URL", "https://windmill.intermesh.net/api/w/indiamart-workspace")
_BATCH_FLOW_PATH = os.getenv("WINDMILL_BATCH_FLOW_PATH", "u/anuragupadhyay2/Auditor_Batch")
_CONNECT_TIMEOUT = 15.0
_READ_TIMEOUT = 30.0


def _headers() -> dict:
    token = os.getenv("WINDMILL_KEY", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def trigger_batch(offer_ids: list[str]) -> str:
    """Trigger Auditor_Batch flow async. Returns Windmill job UUID."""
    payload = {"offer_id": ",".join(offer_ids)}
    url = f"{WINDMILL_BASE_URL}/jobs/run/f/{_BATCH_FLOW_PATH}"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            json=payload,
            headers=_headers(),
            timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
        )
        resp.raise_for_status()
        text = resp.text.strip()
        return text.strip('"')


async def get_job_status(job_id: str) -> dict:
    """Poll job. Returns completed result or partial progress count.

    - /jobs/completed/get/{uuid}: 200 when done, 404 while running.
    - /jobs_u/get/{uuid}: returns in-progress job with flow_status while running.
    """
    async with httpx.AsyncClient() as client:
        # 1. Check if done
        done_resp = await client.get(
            f"{WINDMILL_BASE_URL}/jobs/completed/get/{job_id}",
            headers=_headers(),
            timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
        )
        if done_resp.status_code == 200:
            job = done_resp.json()
            result = job.get("result")
            # for-loop result is a list; wrap errors per-item
            return {
                "completed": True,
                "success": job.get("success", False),
                "result": result,
                "duration_ms": job.get("duration_ms"),
            }

        # 2. Still running — try to get partial progress from flow_status
        processed = 0
        try:
            prog_resp = await client.get(
                f"{WINDMILL_BASE_URL}/jobs_u/get/{job_id}",
                headers=_headers(),
                timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
            )
            if prog_resp.status_code == 200:
                flow_status = prog_resp.json().get("flow_status", {})
                for mod in flow_status.get("modules", []):
                    if mod.get("id") == "d":  # the for-loop module
                        processed = len(mod.get("flow_jobs", []))
                        break
        except Exception:
            pass

        return {"completed": False, "success": None, "result": None, "processed": processed}
