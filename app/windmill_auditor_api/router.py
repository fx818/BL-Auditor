import asyncio
import logging
import re

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.windmill_auditor_api.service import get_job_status, trigger_batch
# from app.windmill_auditor_api.sheets_service import save_to_sheet

log = logging.getLogger("bl-auditor.windmill-batch")
router = APIRouter(prefix="/windmill-batch", tags=["windmill-batch"])
templates = Jinja2Templates(directory="app/templates")

_POLL_INTERVAL = 10   # seconds between status checks
_POLL_TIMEOUT  = 600  # give up after 10 minutes


async def _poll_and_save(job_id: str, offer_id: str) -> None:
    """Background task: poll Windmill until the job completes, then save to sheet."""
    elapsed = 0
    while elapsed < _POLL_TIMEOUT:
        await asyncio.sleep(_POLL_INTERVAL)
        elapsed += _POLL_INTERVAL
        try:
            status = await get_job_status(job_id)
        except Exception as exc:
            log.warning("poll error job=%s elapsed=%ds: %s", job_id, elapsed, exc)
            continue
        if not status.get("completed"):
            log.debug("job=%s still running processed=%s elapsed=%ds", job_id, status.get("processed"), elapsed)
            continue
        results = status.get("result") or []
        if not isinstance(results, list):
            results = [results]
        # try:
        #     await save_to_sheet(job_id, [offer_id], results)
        #     log.info("sheet saved job=%s offer_id=%s", job_id, offer_id)
        # except Exception as exc:
        #     log.error("sheet save failed job=%s offer_id=%s: %s", job_id, offer_id, exc)
        return
    log.error("poll timeout job=%s offer_id=%s after %ds", job_id, offer_id, _POLL_TIMEOUT)


class TriggerRequest(BaseModel):
    offer_ids: str


class TriggerOneRequest(BaseModel):
    offer_id: str


class SaveRequest(BaseModel):
    job_id: str
    offer_ids: list[str]
    results: list


@router.get("", response_class=HTMLResponse)
async def windmill_batch_page(request: Request):
    return templates.TemplateResponse(request, "windmill_batch.html", {})


@router.post("/trigger-one")
async def trigger_one(body: TriggerOneRequest, background_tasks: BackgroundTasks):
    offer_id = body.offer_id.strip()
    if not offer_id:
        raise HTTPException(status_code=400, detail="offer_id is required")
    try:
        job_id = await trigger_batch([offer_id])
    except Exception as exc:
        log.error("Windmill trigger-one failed offer_id=%s: %s: %s", offer_id, type(exc).__name__, exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Windmill error: {type(exc).__name__}: {exc}")
    background_tasks.add_task(_poll_and_save, job_id, offer_id)
    log.info("Windmill trigger-one: offer_id=%s job=%s (polling in background)", offer_id, job_id)
    return JSONResponse({"job_id": job_id, "offer_id": offer_id})


@router.post("/trigger")
async def trigger(body: TriggerRequest):
    raw = body.offer_ids.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="No offer IDs provided")

    ids = [x.strip() for x in re.split(r"[,\n]+", raw) if x.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="No valid offer IDs parsed")

    try:
        job_id = await trigger_batch(ids)
    except Exception as exc:
        log.error("Windmill batch trigger failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Windmill error: {exc}")

    log.info("Windmill batch triggered: %d offers, job=%s", len(ids), job_id)
    return JSONResponse({"job_id": job_id, "offer_count": len(ids)})


@router.get("/result/{job_id}")
async def result(job_id: str):
    try:
        status = await get_job_status(job_id)
    except Exception as exc:
        log.error("Windmill poll failed job=%s: %s: %s", job_id, type(exc).__name__, exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Windmill poll error: {type(exc).__name__}: {exc}")
    return JSONResponse(status)


# @router.post("/save")
# async def save(body: SaveRequest):
#     """Called by frontend once when results are ready. Stateless — safe with multiple workers."""
#     results = body.results if isinstance(body.results, list) else [body.results]
#     try:
#         await save_to_sheet(body.job_id, body.offer_ids, results)
#     except Exception as exc:
#         log.error("Sheet save failed for job %s: %s", body.job_id, exc)
#         raise HTTPException(status_code=502, detail=str(exc))
#     return JSONResponse({"saved": True})
