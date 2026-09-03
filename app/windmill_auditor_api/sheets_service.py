import json
import logging
import os
from datetime import datetime, timezone

import httpx

log = logging.getLogger("bl-auditor.sheets")

_WEBHOOK_URL = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL", "")

_PRIORITY_COLS = [
    "timestamp", "job_id", "offer_id", "_status", "_error",
    "Decision Verdict", "Decision Reason",
    "Retail_Agent_classification",
    "Inter_ISQ_Agent_status", "Inter_ISQ_Agent_reason",
    "Title_ISQ_Agent_status", "Title_ISQ_Agent_reason",
    "ISQ_vs_MCAT_Agent_status", "ISQ_vs_MCAT_Agent_reason",
    "Buyer_Viewed_Agent_status",
    "Title_vs_MCAT_Agent_status",
    "BL_Profile_Agent_status", "BL_Profile_Agent_reason",
    "Description_Agent_status",
]


def _build_payload(job_id: str, offer_ids: list, results: list) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    records = []
    for i, offer_id in enumerate(offer_ids):
        row = results[i] if i < len(results) else None
        if row is None:
            rec = {"offer_id": offer_id, "_status": "no_result", "_error": "No result returned by flow"}
        elif not isinstance(row, dict):
            # Unexpected type (list, str, etc.) — serialise as JSON so it's readable in the sheet
            rec = {"offer_id": offer_id, "_status": "unexpected",
                   "_error": f"Unexpected result type: {type(row).__name__}",
                   "raw_result": json.dumps(row, ensure_ascii=False)}
        elif "error" in row:
            err = row["error"]
            err_msg = err if isinstance(err, str) else (err.get("message") if isinstance(err, dict) else str(err))
            rec = {"offer_id": offer_id, "_status": "error", "_error": err_msg, **row}
        else:
            rec = {"offer_id": offer_id, "_status": "success", "_error": "", **row}
        rec["timestamp"] = now
        rec["job_id"] = job_id
        records.append(rec)

    extra, seen = [], set(_PRIORITY_COLS)
    for rec in records:
        for k in rec:
            if k not in seen:
                seen.add(k)
                extra.append(k)
    header = _PRIORITY_COLS + extra

    rows = [[str(rec.get(col, "")) for col in header] for rec in records]
    return {"header": header, "rows": rows}


async def save_to_sheet(job_id: str, offer_ids: list, results: list):
    url = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL", _WEBHOOK_URL).strip()
    if not url:
        raise RuntimeError("GOOGLE_SHEETS_WEBHOOK_URL env var is not set")

    payload = _build_payload(job_id, offer_ids, results)

    async with httpx.AsyncClient() as client:
        # Google Apps Script POSTs return a 302 to an echo URL that only accepts GET.
        resp = await client.post(url, json=payload, timeout=30, follow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            redirect_url = resp.headers.get("location", "")
            if redirect_url:
                resp = await client.get(redirect_url, timeout=30)
        resp.raise_for_status()

    log.info("Saved %d rows to Google Sheet for job %s", len(payload["rows"]), job_id)
