import json
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.models.schemas import AuditPayload
from app.services.audit_log_service import append_audit_dashboard_row, read_audit_dashboard_rows
from app.services.auditor_service import call_auditor_api
from app.services.buylead_service import (
    DEFAULT_AUDIT_PAYLOAD,
    build_audit_payload_from_buylead,
    fetch_buylead_detail,
)

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

    try:
        buylead_response = await fetch_buylead_detail(offer_id)
        payload = build_audit_payload_from_buylead(offer_id, buylead_response)
        result = await call_auditor_api(payload)
        csv_path = append_audit_dashboard_row(offer_id, payload, result)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Upstream API error: {exc}")

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
        },
    )


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
        },
    )
