import asyncio
import json
import os
import time
from typing import Any, Dict

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
from app.buyer_profile_agent import run_buyer_profile_agent

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
    buylead_response = {}
    payload = {}
    failed_step = None
    fatal_exc = None
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
            failed_step = "BuyLead API"
            fatal_exc = exc
            raise

        # Step 2: Payload Build
        try:
            payload = build_audit_payload_from_buylead(offer_id, buylead_response)
            bl_data = buylead_response.get("RESPONSE", {}).get("DATA", {})
            trace.add_step("Payload Build", "transform",
                input_={
                    "ETO_OFR_TITLE": bl_data.get("ETO_OFR_TITLE"),
                    "PRIME_MCAT_NAME": bl_data.get("PRIME_MCAT_NAME"),
                    "FK_GLCAT_MCAT_ID": bl_data.get("FK_GLCAT_MCAT_ID"),
                    "ETO_OFR_APPROX_ORDER_VALUE": bl_data.get("ETO_OFR_APPROX_ORDER_VALUE"),
                    "ETO_OFR_DESC": bl_data.get("ETO_OFR_DESC"),
                    "ENRICHMENTINFO": bl_data.get("ENRICHMENTINFO"),
                },
                output=payload,
            )
        except Exception as exc:
            trace.add_step("Payload Build", "transform", error=exc)
            failed_step = "Payload Build"
            fatal_exc = exc
            raise

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
            failed_step = "Audit API"
            fatal_exc = exc
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

        # Step 6: Buyer Profile Agent
        t0 = time.monotonic()
        try:
            buyer_state = await run_buyer_profile_agent(offer_id, buylead_response, _trace=True)
            buyer_result = buyer_state["result"]
            trace.add_step("Buyer Profile Agent", "llm_agent",
                input_=buyer_state.get("agent_input", {}),
                raw_output=buyer_state.get("raw_output", ""),
                parsed=buyer_result,
                duration_ms=int((time.monotonic() - t0) * 1000),
                llm_messages={
                    "system": buyer_state.get("system_prompt", ""),
                    "user": buyer_state.get("user_message", ""),
                },
                sub_steps=buyer_state.get("sub_steps", []),
            )
        except Exception as buyer_exc:
            buyer_result = {
                "Display_id": offer_id,
                "Genuineness": "Unverifiable",
                "Profile_Score": None,
                "Confidence": "None",
                "Profile_Reason": "Buyer profile classification failed; see buyer_error.",
                "error": str(buyer_exc),
            }
            trace.add_step("Buyer Profile Agent", "llm_agent",
                error=buyer_exc,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

        try:
            trace_id = trace.save(
                item_name=result.get("item_name") or payload.get("item_name", ""),
                mcat_name=payload.get("mcat_name", ""),
            )
        except Exception:
            trace_id = None
        csv_path = append_audit_dashboard_row(offer_id, payload, result, retail_result, price_result, buyer_result, trace_id=trace_id)
    except Exception:
        try:
            trace_id = trace.save(
                item_name=(payload or {}).get("item_name", ""),
                mcat_name=(payload or {}).get("mcat_name", ""),
            )
        except Exception:
            trace_id = None
        return templates.TemplateResponse(
            "audit_error.html",
            {
                "request": request,
                "offer_id": offer_id,
                "failed_step": failed_step or "Unknown",
                "error_message": str(fatal_exc) if fatal_exc else "Unknown error",
                "friendly_error": _friendly_error(fatal_exc) if fatal_exc else "Audit pipeline error",
                "steps": trace.steps,
                "trace_id": trace_id,
                "buylead_response": buylead_response,
                "buylead_raw_json": json.dumps(buylead_response, indent=2, ensure_ascii=False) if buylead_response else "",
                "payload": payload or {},
                "payload_json": json.dumps(payload, indent=2, ensure_ascii=False) if payload else "",
            },
        )

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
            "buyer_result": buyer_result,
            "buyer_raw_json": json.dumps(buyer_result, indent=2, ensure_ascii=False),
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


_RETAIL_FAIL = {
    "Classification": "UNCLASSIFIED", "Classi_Score": None, "Confidence": "None",
    "Override_Applied": "No", "Reason": "Retail agent did not run for this trace.",
}
_PRICE_FAIL = {
    "Price_Results": "Unverifiable", "Price_Score": None, "Confidence": "None",
    "Price_Value_By_AI": "", "Price_Reason": "Price agent did not run for this trace.",
}
_BUYER_FAIL = {
    "Genuineness": "Unverifiable", "Profile_Score": None, "Confidence": "None",
    "Profile_Reason": "Buyer profile agent did not run for this trace.",
}


def _agent_result_from_step(step: Dict[str, Any], offer_id: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    parsed = step.get("parsed") if isinstance(step, dict) else None
    if isinstance(parsed, dict) and parsed:
        return parsed
    err = step.get("error") if isinstance(step, dict) else None
    base = {"Display_id": offer_id, **fallback}
    if err:
        base["error"] = str(err)
    return base


@router.get("/traces/{trace_id}/detail", response_class=HTMLResponse)
async def trace_detail_view(request: Request, trace_id: str):
    """Re-renders the single-audit dashboard from a saved trace."""
    trace = get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    steps_by_name: Dict[str, Dict[str, Any]] = {}
    for step in trace.get("steps", []) or []:
        if isinstance(step, dict) and step.get("name"):
            steps_by_name[step["name"]] = step

    bl_step = steps_by_name.get("BuyLead API", {})
    payload_step = steps_by_name.get("Payload Build", {})
    audit_step = steps_by_name.get("Audit API", {})
    retail_step = steps_by_name.get("Retail Agent", {})
    price_step = steps_by_name.get("Price Agent", {})
    buyer_step = steps_by_name.get("Buyer Profile Agent", {})

    offer_id = trace.get("offer_id", "")
    buylead_response = bl_step.get("output") if isinstance(bl_step.get("output"), dict) else {}
    payload = payload_step.get("output") if isinstance(payload_step.get("output"), dict) else {}
    audit_result = audit_step.get("output") if isinstance(audit_step.get("output"), dict) else {}

    fatal_step = None
    fatal_error = None
    for name in ("BuyLead API", "Payload Build", "Audit API"):
        s = steps_by_name.get(name)
        if isinstance(s, dict) and s.get("status") == "error":
            fatal_step = name
            fatal_error = s.get("error", "")
            break

    if fatal_step:
        return templates.TemplateResponse(
            "audit_error.html",
            {
                "request": request,
                "offer_id": offer_id,
                "failed_step": fatal_step,
                "error_message": fatal_error or "Unknown error",
                "friendly_error": f"{fatal_step} failed for this trace.",
                "steps": trace.get("steps", []),
                "trace_id": trace_id,
                "buylead_response": buylead_response,
                "buylead_raw_json": "",
                "payload": payload or {},
                "payload_json": "",
            },
        )

    retail_result = _agent_result_from_step(retail_step, offer_id, _RETAIL_FAIL)
    price_result = _agent_result_from_step(price_step, offer_id, _PRICE_FAIL)
    buyer_result = _agent_result_from_step(buyer_step, offer_id, _BUYER_FAIL)

    def _dump(value: Any) -> str:
        if value in (None, ""):
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "data": audit_result,
            "payload": payload,
            "offer_id": offer_id,
            "buylead_response": buylead_response,
            "csv_path": "",
            "retail_result": retail_result,
            "price_result": price_result,
            "buyer_result": buyer_result,
            "trace_id": trace_id,
            "is_detail_view": True,
            "buylead_raw_json": _dump(buylead_response),
            "raw_json": _dump(audit_result),
            "retail_raw_json": _dump(retail_result),
            "price_raw_json": _dump(price_result),
            "buyer_raw_json": _dump(buyer_result),
        },
    )


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
                yield _sse({"index": i, "total": total, "offer_id": offer_id, "error": "Invalid offer ID — must be numeric", "failed_step": "Validation", "steps": []})
                continue
            trace = AuditTrace(offer_id)
            buylead_response = {}
            payload = {}
            failed_step = None
            try:
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
                    failed_step = "BuyLead API"
                    raise

                try:
                    payload = build_audit_payload_from_buylead(offer_id, buylead_response)
                    trace.add_step("Payload Build", "transform", output=payload)
                except Exception as exc:
                    trace.add_step("Payload Build", "transform", error=exc)
                    failed_step = "Payload Build"
                    raise

                t0 = time.monotonic()
                result, retail_raw, price_raw, buyer_raw = await asyncio.gather(
                    call_auditor_api(payload),
                    run_retail_agent(offer_id, buylead_response),
                    run_price_agent(offer_id, buylead_response),
                    run_buyer_profile_agent(offer_id, buylead_response),
                    return_exceptions=True,
                )

                if isinstance(result, Exception):
                    trace.add_step("Audit API", "api_call",
                        endpoint=AUDITOR_API_URL,
                        input_=payload,
                        error=result,
                        duration_ms=int((time.monotonic() - t0) * 1000),
                    )
                    failed_step = "Audit API"
                    raise result
                trace.add_step("Audit API", "api_call",
                    endpoint=AUDITOR_API_URL,
                    input_=payload,
                    output=result,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )

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
                buyer_result = buyer_raw if isinstance(buyer_raw, dict) else {
                    "Display_id": offer_id,
                    "Genuineness": "Unverifiable",
                    "Profile_Score": None, "Confidence": "None",
                    "Profile_Reason": "Buyer profile agent failed.",
                    "error": str(buyer_raw),
                }

                try:
                    ok_trace_id = trace.save(
                        item_name=result.get("item_name") or payload.get("item_name", ""),
                        mcat_name=payload.get("mcat_name", ""),
                    )
                except Exception:
                    ok_trace_id = None

                append_audit_dashboard_row(offer_id, payload, result, retail_result, price_result, buyer_result, trace_id=ok_trace_id)

                def _nested(src, *keys):
                    v = src
                    for k in keys:
                        v = v.get(k, "") if isinstance(v, dict) else ""
                    return v or ""

                yield _sse({
                    "index": i,
                    "total": total,
                    "offer_id": offer_id,
                    "trace_id": ok_trace_id,
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
                    "buyer_profile_genuineness": buyer_result.get("Genuineness", ""),
                    "buyer_profile_score": buyer_result.get("Profile_Score", ""),
                    "buyer_profile_confidence": buyer_result.get("Confidence", ""),
                    "buyer_profile_reason": buyer_result.get("Profile_Reason", ""),
                    "buyer_error": buyer_result.get("error", ""),
                })
            except Exception as exc:
                errors += 1
                try:
                    err_trace_id = trace.save(
                        item_name=(payload or {}).get("item_name", ""),
                        mcat_name=(payload or {}).get("mcat_name", ""),
                    )
                except Exception:
                    err_trace_id = None
                yield _sse({
                    "index": i,
                    "total": total,
                    "offer_id": offer_id,
                    "error": str(exc),
                    "failed_step": failed_step or "Unknown",
                    "steps": trace.steps,
                    "trace_id": err_trace_id,
                })

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
    buyer_result = {
        "Display_id": "142764424452",
        "Genuineness": "Unverifiable",
        "Profile_Score": None,
        "Confidence": "None",
        "Profile_Reason": "Demo page does not call the buyer profile agent.",
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
            "buyer_result": buyer_result,
            "buyer_raw_json": json.dumps(buyer_result, indent=2),
        },
    )
