import asyncio
import json
import os
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.models.schemas import AuditPayload
from app.services.audit_log_service import append_audit_dashboard_row, read_audit_dashboard_rows
from app.services.auditor_service import API_URL as AUDITOR_API_URL, call_auditor_api
from app.services.buylead_service import (
    BUYLEAD_API_URL,
    DEFAULT_AUDIT_PAYLOAD,
    build_audit_payload_from_buylead,
    fetch_buylead_detail,
)
from app.services.trace_service import AuditTrace, get_trace, list_traces
from app.retail_agent import run_retail_agent
from app.price_agent import run_price_agent

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serves the offer-id input page."""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "sample_offer_id": "142764424452",
            "default_payload": json.dumps(DEFAULT_AUDIT_PAYLOAD, indent=2),
        },
    )


@router.get("/records", response_class=HTMLResponse)
async def records(request: Request):
    """Shows previously saved dashboard records from the CSV log."""
    return templates.TemplateResponse(
        "records.html",
        {
            "request": request,
            "rows": read_audit_dashboard_rows(),
        },
    )


@router.post("/audit", response_class=HTMLResponse)
async def audit(request: Request):
    """Fetches BuyLead data, builds the payload, calls the auditor API, and renders the result page."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")

    offer_id = str(body.get("offer_id", "")).strip()
    if not offer_id:
        raise HTTPException(status_code=400, detail="Offer ID is required")
    if not offer_id.isdigit():
        raise HTTPException(status_code=400, detail="Offer ID must be numeric")

    trace = AuditTrace(offer_id)
    try:
        # Step 1: BuyLead API
        t0 = time.monotonic()
        try:
            buylead_response = await fetch_buylead_detail(offer_id)
            trace.add_step("BuyLead API", "api_call",
                endpoint=BUYLEAD_API_URL,
                input_={"offer_id": offer_id},
                output=buylead_response,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as exc:
            trace.add_step("BuyLead API", "api_call",
                endpoint=BUYLEAD_API_URL,
                input_={"offer_id": offer_id},
                error=exc,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            raise

        # Step 2: Payload Build
        payload = build_audit_payload_from_buylead(offer_id, buylead_response)
        bl_data = buylead_response.get("RESPONSE", {}).get("DATA", {})
        trace.add_step("Payload Build", "transform",
            input_={
                "ETO_OFR_TITLE": bl_data.get("ETO_OFR_TITLE"),
                "PRIME_MCAT_NAME": bl_data.get("PRIME_MCAT_NAME"),
                "MCAT_IDS": bl_data.get("MCAT_IDS"),
                "ETO_OFR_APPROX_ORDER_VALUE": bl_data.get("ETO_OFR_APPROX_ORDER_VALUE"),
                "ETO_OFR_DESC": bl_data.get("ETO_OFR_DESC"),
                "ENRICHMENTINFO": bl_data.get("ENRICHMENTINFO"),
            },
            output=payload,
        )

        # Step 3: Audit API
        t0 = time.monotonic()
        try:
            result = await call_auditor_api(payload)
            trace.add_step("Audit API", "api_call",
                endpoint=AUDITOR_API_URL,
                input_=payload,
                output=result,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as exc:
            trace.add_step("Audit API", "api_call",
                endpoint=AUDITOR_API_URL,
                input_=payload,
                error=exc,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            raise

        # Step 4: Retail Agent
        t0 = time.monotonic()
        try:
            retail_state = await run_retail_agent(offer_id, buylead_response, _trace=True)
            retail_result = retail_state["result"]
            trace.add_step("Retail Agent", "llm_agent",
                input_=retail_state.get("agent_input", {}),
                raw_output=retail_state.get("raw_output", ""),
                parsed=retail_result,
                duration_ms=int((time.monotonic() - t0) * 1000),
                llm_messages={
                    "system": retail_state.get("system_prompt", ""),
                    "user": retail_state.get("user_message", ""),
                },
                sub_steps=retail_state.get("sub_steps", []),
            )
        except Exception as retail_exc:
            retail_result = {
                "Display_id": offer_id, "Classification": "UNCLASSIFIED",
                "Classi_Score": None, "Confidence": "None",
                "Override_Applied": "No",
                "Reason": "Retail classification failed; see retail_error.",
                "error": str(retail_exc),
            }
            trace.add_step("Retail Agent", "llm_agent",
                error=retail_exc,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

        # Step 5: Price Agent
        t0 = time.monotonic()
        try:
            price_state = await run_price_agent(offer_id, buylead_response, _trace=True)
            price_result = price_state["result"]
            trace.add_step("Price Agent", "llm_agent",
                input_=price_state.get("agent_input", {}),
                raw_output=price_state.get("raw_output", ""),
                parsed=price_result,
                duration_ms=int((time.monotonic() - t0) * 1000),
                llm_messages={
                    "system": price_state.get("system_prompt", ""),
                    "user": price_state.get("user_message", ""),
                },
                sub_steps=price_state.get("sub_steps", []),
            )
        except Exception as price_exc:
            price_result = {
                "Display_id": offer_id, "Price_Results": "Unverifiable",
                "Price_Score": None, "Confidence": "None",
                "Price_Value_By_AI": "",
                "Price_Reason": "Price agent failed; see price_error.",
                "error": str(price_exc),
            }
            trace.add_step("Price Agent", "llm_agent",
                error=price_exc,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

        csv_path = append_audit_dashboard_row(offer_id, payload, result, retail_result, price_result)
        try:
            trace_id = trace.save(
                item_name=result.get("item_name") or payload.get("item_name", ""),
                mcat_name=payload.get("mcat_name", ""),
            )
        except Exception:
            trace_id = None
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_friendly_error(exc))

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "data": result,
            "payload": payload,
            "offer_id": offer_id,
            "buylead_response": buylead_response,
            "buylead_raw_json": json.dumps(buylead_response, indent=2),
            "csv_path": csv_path,
            "raw_json": json.dumps(result, indent=2),
            "retail_result": retail_result,
            "retail_raw_json": json.dumps(retail_result, indent=2, ensure_ascii=False),
            "price_result": price_result,
            "price_raw_json": json.dumps(price_result, indent=2, ensure_ascii=False),
            "trace_id": trace_id,
        },
    )


@router.get("/traces", response_class=HTMLResponse)
async def traces_list(request: Request):
    return templates.TemplateResponse("traces.html", {
        "request": request,
        "traces": list_traces(),
    })


@router.get("/traces/{trace_id}", response_class=HTMLResponse)
async def trace_detail(request: Request, trace_id: str):
    trace = get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return templates.TemplateResponse("trace_detail.html", {
        "request": request,
        "trace": trace,
    })


@router.get("/batch", response_class=HTMLResponse)
async def batch_page(request: Request):
    return templates.TemplateResponse("batch.html", {"request": request})


@router.get("/batch/stream")
async def batch_stream(offer_ids: str = ""):
    ids = [oid.strip() for oid in offer_ids.split(",") if oid.strip()]
    total = len(ids)

    async def generate():
        errors = 0
        for i, offer_id in enumerate(ids, 1):
            if not offer_id.isdigit():
                errors += 1
                yield _sse({"index": i, "total": total, "offer_id": offer_id, "error": "Invalid offer ID — must be numeric"})
                continue
            try:
                buylead_response = await fetch_buylead_detail(offer_id)
                payload = build_audit_payload_from_buylead(offer_id, buylead_response)
                result, retail_raw, price_raw = await asyncio.gather(
                    call_auditor_api(payload),
                    run_retail_agent(offer_id, buylead_response),
                    run_price_agent(offer_id, buylead_response),
                    return_exceptions=True,
                )

                if isinstance(result, Exception):
                    raise result

                retail_result = retail_raw if isinstance(retail_raw, dict) else {
                    "Display_id": offer_id, "Classification": "UNCLASSIFIED",
                    "Classi_Score": None, "Confidence": "None",
                    "Override_Applied": "No",
                    "Reason": "Retail agent failed.", "error": str(retail_raw),
                }
                price_result = price_raw if isinstance(price_raw, dict) else {
                    "Display_id": offer_id, "Price_Results": "Unverifiable",
                    "Price_Score": None, "Confidence": "None",
                    "Price_Value_By_AI": "",
                    "Price_Reason": "Price agent failed.", "error": str(price_raw),
                }

                append_audit_dashboard_row(offer_id, payload, result, retail_result, price_result)

                def _nested(src, *keys):
                    v = src
                    for k in keys:
                        v = v.get(k, "") if isinstance(v, dict) else ""
                    return v or ""

                yield _sse({
                    "index": i,
                    "total": total,
                    "offer_id": offer_id,
                    "item_name": result.get("item_name") or payload.get("item_name", ""),
                    "mcat_name": payload.get("mcat_name", ""),
                    "price": payload.get("price", ""),
                    "specs_category_outlier_status": _nested(result, "specs_category_outlier", "status"),
                    "specs_category_outlier_reason": _nested(result, "specs_category_outlier", "reason"),
                    "title_category_outlier_status": _nested(result, "title_category_outlier", "status"),
                    "title_category_outlier_reason": _nested(result, "title_category_outlier", "reason"),
                    "photo_category_outlier_status": _nested(result, "photo_category_outlier", "status"),
                    "photo_category_outlier_reason": _nested(result, "photo_category_outlier", "reason"),
                    "photo_title_verdict": _nested(result, "photo_title_verdict", "final_verdict"),
                    "photo_title_verdict_reason": result.get("photo_title_verdict_reason", ""),
                    "title_spec_verdict": _nested(result, "title_spec_verdict", "final_verdict"),
                    "title_spec_verdict_reason": result.get("title_spec_verdict_reason", ""),
                    "price_flag_verdict": _nested(result, "price_flag", "final_verdict"),
                    "price_flag_reason": _nested(result, "price_flag", "reason"),
                    "retail_classification": retail_result.get("Classification", ""),
                    "retail_classi_score": retail_result.get("Classi_Score", ""),
                    "retail_confidence": retail_result.get("Confidence", ""),
                    "retail_override_applied": retail_result.get("Override_Applied", ""),
                    "retail_reason": retail_result.get("Reason", ""),
                    "retail_error": retail_result.get("error", ""),
                    "price_agent_result": price_result.get("Price_Results", ""),
                    "price_agent_score": price_result.get("Price_Score", ""),
                    "price_agent_confidence": price_result.get("Confidence", ""),
                    "price_value_by_ai": price_result.get("Price_Value_By_AI", ""),
                    "price_agent_reason": price_result.get("Price_Reason", ""),
                    "price_error": price_result.get("error", ""),
                })
            except Exception as exc:
                errors += 1
                yield _sse({"index": i, "total": total, "offer_id": offer_id, "error": str(exc)})

            await asyncio.sleep(0)

        yield _sse({"done": True, "total": total, "errors": errors})

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _friendly_error(exc: Exception) -> str:
    msg = str(exc)
    if "codec can't encode" in msg or "charmap" in msg:
        return "A character encoding error occurred while saving the trace. The audit result was processed — please retry."
    if "Connection" in msg or "ConnectError" in msg or "ConnectTimeout" in msg:
        return "Could not reach an upstream API. Check network connectivity and try again."
    if "HTTPStatusError" in msg or "status_code" in msg:
        return f"Upstream API returned an error response. Detail: {msg[:200]}"
    if "Missing" in msg and "LLM" in msg:
        return msg
    return f"Audit pipeline error: {msg[:300]}"


@router.post("/api/audit")
async def api_audit(payload: AuditPayload):
    """Raw JSON API endpoint - returns the upstream response directly."""
    try:
        result = await call_auditor_api(payload.model_dump())
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/demo", response_class=HTMLResponse)
async def demo(request: Request):
    """Renders the result page using the local resp.json sample file."""
    demo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "resp.json"))
    buylead_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "response.json"))

    try:
        with open(os.path.normpath(demo_path), "r", encoding="utf-8") as audit_file:
            result = json.load(audit_file)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="resp.json not found")

    try:
        with open(os.path.normpath(buylead_path), "r", encoding="utf-8") as buylead_file:
            buylead_response = json.load(buylead_file)
    except FileNotFoundError:
        buylead_response = {"note": "response.json not found"}

    payload = build_audit_payload_from_buylead("142764424452", buylead_response)
    retail_result = {
        "Display_id": "142764424452",
        "Classification": "UNCLASSIFIED",
        "Classi_Score": None,
        "Confidence": "None",
        "Override_Applied": "No",
        "Reason": "Demo page does not call the retail agent.",
    }
    price_result = {
        "Display_id": "142764424452",
        "Price_Results": "Unverifiable",
        "Price_Score": None,
        "Confidence": "None",
        "Price_Value_By_AI": "",
        "Price_Reason": "Demo page does not call the price agent.",
    }

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "data": result,
            "payload": payload,
            "offer_id": "142764424452",
            "buylead_response": buylead_response,
            "buylead_raw_json": json.dumps(buylead_response, indent=2),
            "csv_path": "",
            "raw_json": json.dumps(result, indent=2),
            "retail_result": retail_result,
            "retail_raw_json": json.dumps(retail_result, indent=2),
            "price_result": price_result,
            "price_raw_json": json.dumps(price_result, indent=2),
        },
    )
