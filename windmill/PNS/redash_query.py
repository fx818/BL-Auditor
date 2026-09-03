import time
import requests

REDASH_BASE = "https://redash.intermesh.net"


def main(
    api_key: str = "YOUR_API_KEY",
    query_id: int = 42046,
    params: dict = {},
):
    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json",
    }

    # Trigger execution with parameters
    resp = requests.post(
        f"{REDASH_BASE}/api/queries/{query_id}/results",
        headers=headers,
        json={"parameters": params},
        timeout=30,
    )
    resp.raise_for_status()
    trigger = resp.json()

    # Redash returns either a cached result directly or a job to poll
    if "query_result" in trigger:
        return trigger["query_result"]["data"]["rows"]

    job_id = trigger["job"]["id"]

    # Poll until done (max 120s)
    query_result_id = None
    for _ in range(40):
        resp = requests.get(
            f"{REDASH_BASE}/api/jobs/{job_id}",
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        job = resp.json()["job"]

        if job["status"] == 3:
            query_result_id = job["query_result_id"]
            break
        elif job["status"] == 4:
            raise RuntimeError(f"Query failed: {job.get('error')}")
        elif job["status"] == 5:
            raise RuntimeError("Query was cancelled")

        time.sleep(3)

    if query_result_id is None:
        raise TimeoutError("Query did not complete within 120 seconds")

    # Fetch rows
    resp = requests.get(
        f"{REDASH_BASE}/api/query_results/{query_result_id}.json",
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["query_result"]["data"]["rows"]
