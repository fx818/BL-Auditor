import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger("bl-auditor.audit")

_DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parents[2])
_BATCH_OFFER_TIMEOUT = float(os.getenv("BATCH_OFFER_TIMEOUT", "300"))

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.models.schemas import AuditPayload
from app.services.audit_log_service import append_audit_dashboard_row, clear_audit_dashboard_log, read_audit_dashboard_rows, _scoring_breakdown_fields, _compute_bl_quality_1, _compute_bl_quality_2, _compute_bl_lq_completeness, _ensure_csv_headers
from app.services.auditor_service import WINDMILL_BASE_URL as AUDITOR_API_URL, call_windmill_agents
from app.services.buylead_service import (
    BUYLEAD_API_URL,
    DEFAULT_AUDIT_PAYLOAD,
    build_audit_payload_from_buylead,
    extract_existing_retail_flag,
    fetch_buylead_detail,
)
from app.services.buyer_activity_service import (
    BUYER_ACTIVITY_API_URL,
    BuyerActivityError,
    DEFAULT_AK as BUYER_ACTIVITY_AK,
    DEFAULT_GLID as BUYER_ACTIVITY_GLID,
    default_endlogtime,
    default_logtime,
    fetch_buyer_activity,
    parse_buyer_activity,
    window_from_bl_data,
)
from app.services.glid_crypto_service import decrypt_glid
from app.services.langfuse_service import make_langfuse_handler, set_langfuse_handler, reset_langfuse_handler
from app.services.prompt_override_service import (
    ADMIN_ONLY_AGENTS as PROMPT_ADMIN_ONLY,
    AGENTS as PROMPT_AGENTS,
    PUBLIC_AGENTS as PROMPT_PUBLIC,
    get_active_prompt,
    get_bundled_prompt,
    reset_audit_user,
    reset_override,
    save_override,
    set_audit_user,
)
from app.services.trace_service import AuditTrace, clear_traces, get_trace, list_traces
from app.buyer_viewed_agent import run_buyer_viewed_agent
from app.buyer_viewed_vs_csl import run_buyer_viewed_vs_csl
from app.buyer_profile_agent import run_buyer_profile_agent
from app.services.buyer_profile_service import fetch_user_detail_for_buylead
from app.retail_agent_2 import run_retail_agent_2
from app.isq_validation_agent import run_isq_validation_agent
from app.description_agent import run_description_agent
from app.description_agent.agent import select_activity_keywords, match_searched_terms
from app.scoring_agent import run_scoring_agent, run_scoring_agent2
from app.completeness_agent import run_completeness_agent
from app.quantity_agent import run_quantity_agent
from app.specs_vs_category_agent2 import run_specs_vs_category_agent2
from app.title_vs_category_agent2 import run_title_vs_category_agent2
from app.title_vs_specs_agent2 import run_title_vs_specs_agent2
from app.recheck_agent import run_recheck_agent, AGENT_NAME_MAP as _RECHECK_AGENT_NAMES
from app.decision_agent import run_decision_agent

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serves the offer-id input page."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "sample_offer_id": "142764424452",
            "default_payload": json.dumps(DEFAULT_AUDIT_PAYLOAD, indent=2),
            "audit_url": "/audit",
            "batch_url": "/batch",
            "records_url": "/records",
            "traces_url": "/traces",
            "windmill_batch_url": "/windmill-batch",
        },
    )


@router.get("/activity", response_class=HTMLResponse)
async def activity_form(request: Request):
    """Buyer-activity timeline viewer — renders the form with defaults.

    Submission goes via POST (see `activity`) so the AK token and params stay
    out of the URL/browser history.
    """
    form = {
        "glusr_id": BUYER_ACTIVITY_GLID,
        "logtime": default_logtime(),
        "endlogtime": default_endlogtime(),
    }
    return templates.TemplateResponse(
        request, "activity.html", {"form": form, "result": None}
    )


@router.post("/activity", response_class=HTMLResponse)
async def activity(request: Request):
    """Fetch buyer activity for the submitted form and render the timeline.

    Reads form fields from the POST body (not the query string) so nothing
    sensitive ends up in the URL. Empty fields fall back to the defaults.
    The AK token is never accepted from the UI — it comes from env (BUYER_ACTIVITY_AK).
    """
    fd = await request.form()
    form = {
        "glusr_id": (fd.get("glusr_id") or BUYER_ACTIVITY_GLID).strip(),
        "logtime": (fd.get("logtime") or default_logtime()).strip(),
        "endlogtime": (fd.get("endlogtime") or default_endlogtime()).strip(),
    }

    try:
        raw = await fetch_buyer_activity(
            glusr_id=form["glusr_id"],
            ak=BUYER_ACTIVITY_AK,
            logtime=form["logtime"],
            endlogtime=form["endlogtime"],
        )
        result = parse_buyer_activity(raw, glusr_id=form["glusr_id"])
    except BuyerActivityError as exc:
        result = {
            "ok": False,
            "status": "ERROR",
            "code": None,
            "message": "",
            "error": str(exc),
        }

    return templates.TemplateResponse(
        request, "activity.html", {"form": form, "result": result}
    )


@router.get("/records", response_class=HTMLResponse)
async def records(request: Request):
    """Shows previously saved dashboard records from the CSV log."""
    return templates.TemplateResponse(
        request,
        "records.html",
        {
            "rows": read_audit_dashboard_rows(),
            "show_retail_1": False,
            "home_url": "/",
            "trace_detail_base": "/traces",
        },
    )


@router.post("/audit", response_class=HTMLResponse)
async def audit(request: Request):
    return await _audit_handler(request, show_retail_1=False)


@router.post("/admin_view/audit", response_class=HTMLResponse)
async def admin_audit(request: Request):
    return await _audit_handler(request, show_retail_1=True)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {})


async def _fetch_buyer_activity_for_bl(buylead_response, trace):
    """Fetch + parse the buyer's activity for the BL window and add the "Buyer
    Activity" trace step. Returns (buyer_activity, buyer_glid). Non-fatal — any
    failure records the trace step and returns (None, buyer_glid_or_None)."""
    data = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {}) or {}
    enc_glid = data.get("FK_GLUSR_USR_ID")
    if not enc_glid:
        return None, None
    buyer_activity = None
    buyer_glid = None
    logt, endt = window_from_bl_data(data)
    t_ba = time.monotonic()
    try:
        buyer_glid = decrypt_glid(str(enc_glid))
        ba_raw = await fetch_buyer_activity(
            glusr_id=buyer_glid, ak=BUYER_ACTIVITY_AK, logtime=logt, endlogtime=endt,
        )
        buyer_activity = parse_buyer_activity(ba_raw, glusr_id=buyer_glid)
        trace.add_step("Buyer Activity", "api_call", endpoint=BUYER_ACTIVITY_API_URL,
            input_={"glusrId": buyer_glid, "logtime": logt, "endlogtime": endt},
            output=ba_raw, duration_ms=int((time.monotonic() - t_ba) * 1000))
    except Exception as _ba_exc:
        trace.add_step("Buyer Activity", "api_call", endpoint=BUYER_ACTIVITY_API_URL,
            input_={"encrypted_glid": str(enc_glid), "decrypted_glid": buyer_glid,
                    "logtime": logt, "endlogtime": endt},
            error=_ba_exc, duration_ms=int((time.monotonic() - t_ba) * 1000))
    return buyer_activity, buyer_glid


async def _enrich_activity_keywords(buyer_activity, buylead_response, payload, desc_result, trace):
    """Desc-dependent enrichment: pick activity keywords + match searched terms.

    Only runs when the description starts with "Buyer Searched for" AND the
    description verdict is Incorrect AND activity is available. Non-fatal.
    Returns (activity_kw, term_match)."""
    activity_kw = None
    term_match = None
    data = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {}) or {}
    desc_text = str(data.get("ETO_OFR_DESC") or payload.get("item_desc") or "").strip()
    desc_status = (desc_result or {}).get("status", "")
    if (desc_text.lower().startswith("buyer searched for")
            and desc_status == "outlier"
            and buyer_activity and buyer_activity.get("ok")):
        t_kw = time.monotonic()
        try:
            activity_kw = await select_activity_keywords(payload.get("mcat_name", ""), buyer_activity)
            trace.add_step("Activity Keywords", "llm_agent",
                input_={"mcat_name": payload.get("mcat_name", ""), "candidates": activity_kw.get("candidates", [])},
                parsed=activity_kw, duration_ms=int((time.monotonic() - t_kw) * 1000))
        except Exception as _kw_exc:
            activity_kw = {"keywords": [], "candidates": [], "reason": "", "error": str(_kw_exc)}
            trace.add_step("Activity Keywords", "llm_agent",
                error=_kw_exc, duration_ms=int((time.monotonic() - t_kw) * 1000))

        # Deterministic (no LLM): split the "Buyer searched for X, Y, Z" desc into
        # terms and report, per term, whether it appears in the activity log.
        t_tm = time.monotonic()
        try:
            term_match = match_searched_terms(desc_text, buyer_activity)
            trace.add_step("Searched Terms Match", "transform",
                input_={"terms": [t["term"] for t in term_match["terms"]],
                        "log_keywords": term_match["log_keywords"]},
                parsed=term_match, duration_ms=int((time.monotonic() - t_tm) * 1000))
        except Exception as _tm_exc:
            trace.add_step("Searched Terms Match", "transform",
                error=_tm_exc, duration_ms=int((time.monotonic() - t_tm) * 1000))
    return activity_kw, term_match


def _audit_actor(request) -> str:
    """Email of the user who triggered the audit; 'admin' for the consumer
    (trusted-peer /audit with no session user)."""
    user = request.session.get("user") if request is not None else None
    return (user or {}).get("email") or "admin"


async def _audit_handler(request: Request, show_retail_1: bool, selected_agents: set | None = None):
    """Fetches BuyLead data, builds the payload, calls the auditor API, and renders the result page."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")

    offer_id = str(body.get("offer_id", "")).strip()
    if selected_agents is None:
        _sa_raw = body.get("selected_agents")
        if isinstance(_sa_raw, list):
            selected_agents = set(_sa_raw)
    if not offer_id:
        raise HTTPException(status_code=400, detail="Offer ID is required")
    if not offer_id.isdigit():
        raise HTTPException(status_code=400, detail="Offer ID must be numeric")

    trace = AuditTrace(offer_id)
    actor = _audit_actor(request)
    _user_token = set_audit_user(actor if "@" in (actor or "") else None)
    _lf_handler = make_langfuse_handler(offer_id, user_id=actor)
    _lf_token = set_langfuse_handler(_lf_handler)
    try:
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

            # Validate BuyLead DATA — null means offer doesn't exist or was deleted
            _raw_data = buylead_response.get("RESPONSE", {}).get("DATA")
            if not isinstance(_raw_data, dict):
                failed_step = "BuyLead API"
                _result_code = (buylead_response.get("RESPONSE") or {}).get("RESULT", "")
                fatal_exc = ValueError(
                    f"BuyLead returned no DATA for offer_id={offer_id} — "
                    f"offer may not exist (RESULT={_result_code!r})"
                )
                raise fatal_exc

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

            # Steps 3–5 run concurrently: Audit API + Buyer Viewed + Retail Agent 2 + remaining agents.
            # BuyLead response + payload are ready; none depend on each other.
            async def _timed(coro):
                t = time.monotonic()
                try:
                    return ("ok", await coro, int((time.monotonic() - t) * 1000))
                except Exception as e:
                    return ("err", e, int((time.monotonic() - t) * 1000))

            async def _noop():
                return ("skipped", None, 0)

            def _pick(key, coro_fn):
                return _timed(coro_fn()) if (selected_agents is None or key in selected_agents) else _noop()

            # User Detail fetched once, shared by the ISQ hard check and buyer-profile agent.
            user_detail = await fetch_user_detail_for_buylead(buylead_response)

            # Buyer activity fetched once (reused by the buyer-profile agent below
            # and the desc-keyword enrichment after the gather). Non-fatal.
            buyer_activity, buyer_glid = await _fetch_buyer_activity_for_bl(buylead_response, trace)

            # Buyer Viewed vs CSL Agent — deterministic, no LLM. Runs after activity is fetched.
            if selected_agents is None or "buyer_viewed_vs_csl" in selected_agents:
                _t_bvvc = time.monotonic()
                buyer_viewed_vs_csl_result = run_buyer_viewed_vs_csl(offer_id, buylead_response, buyer_activity)
                trace.add_step("Buyer Viewed vs CSL", "transform",
                    parsed=buyer_viewed_vs_csl_result,
                    duration_ms=int((time.monotonic() - _t_bvvc) * 1000),
                )
            else:
                buyer_viewed_vs_csl_result = {"status": "not matched", "reason": "Not selected"}

            (
                audit_outcome, buyer_outcome, retail2_outcome,
                isq_outcome, desc_outcome,
                specs_vs_cat_a2_outcome, title_vs_cat_a2_outcome, title_vs_specs_a2_outcome,
                buyer_profile_outcome,
            ) = await asyncio.gather(
                _pick("auditmate",         lambda: call_windmill_agents(payload)),
                _pick("buyer_viewed",      lambda: run_buyer_viewed_agent(offer_id, buylead_response, _trace=True)),
                _pick("retail_agent_2",    lambda: run_retail_agent_2(offer_id, buylead_response, _trace=True)),
                _pick("isq",               lambda: run_isq_validation_agent(offer_id, buylead_response, user_detail=user_detail, _trace=True)),
                _pick("description",       lambda: run_description_agent(offer_id, buylead_response, _trace=True)),
                _pick("specs_vs_category", lambda: run_specs_vs_category_agent2(offer_id, buylead_response, _trace=True)),
                _pick("title_vs_category", lambda: run_title_vs_category_agent2(offer_id, buylead_response, _trace=True)),
                _pick("title_vs_specs",    lambda: run_title_vs_specs_agent2(offer_id, buylead_response, _trace=True)),
                _pick("buyer_profile",     lambda: run_buyer_profile_agent(offer_id, buylead_response, buyer_activity=buyer_activity, user_detail=user_detail, _trace=True)),
            )

            # Step 3: Audit API — record first (fatal step if it failed)
            audit_status, audit_value, audit_ms = audit_outcome
            if audit_status == "ok":
                result = audit_value
                trace.add_step("Audit API", "api_call",
                    endpoint=AUDITOR_API_URL,
                    input_=payload,
                    output=result,
                    duration_ms=audit_ms,
                )
            elif audit_status == "skipped":
                result = {}
            else:
                _err_msg = str(audit_value)[:300]
                result = {
                    "title_category_outlier": {"status": "Error", "reason": _err_msg},
                    "title_spec_verdict":     {"status": "Error", "reason": _err_msg},
                    "specs_category_outlier": {"status": "Error", "reason": _err_msg},
                }
                trace.add_step("Audit API", "api_call",
                    endpoint=AUDITOR_API_URL,
                    input_=payload,
                    error=audit_value,
                    duration_ms=audit_ms,
                )

            # Step 4: Buyer Viewed Agent
            buyer_status, buyer_value, buyer_ms = buyer_outcome
            if buyer_status == "ok":
                buyer_state = buyer_value
                buyer_result = buyer_state["result"]
                trace.add_step("Buyer Viewed Agent", "llm_agent",
                    input_=buyer_state.get("agent_input", {}),
                    raw_output=buyer_state.get("raw_output", ""),
                    parsed=buyer_result,
                    duration_ms=buyer_ms,
                    llm_messages={
                        "system": buyer_state.get("system_prompt", ""),
                        "user": buyer_state.get("user_message", ""),
                    },
                )
            elif buyer_status == "err":
                buyer_exc = buyer_value
                buyer_result = {
                    "status": "not_outlier",
                    "reason": "Buyer viewed classification failed; see buyer_viewed_error.",
                    "product_matched": "0/0",
                    "error": str(buyer_exc),
                }
                trace.add_step("Buyer Viewed Agent", "llm_agent",
                    error=buyer_exc,
                    duration_ms=buyer_ms,
                )
            if buyer_status == "skipped":
                buyer_result = {
                    "status": "not_outlier", "reason": "Not selected", "product_matched": "0/0",
                }

            # Step 5: Retail Agent 2
            retail2_status, retail2_value, retail2_ms = retail2_outcome
            if retail2_status == "ok":
                retail2_state = retail2_value
                retail2_result = retail2_state["result"]
                trace.add_step("Retail Agent 2", "llm_agent",
                    input_=retail2_state.get("agent_input", {}),
                    raw_output=retail2_state.get("raw_output", ""),
                    parsed=retail2_result,
                    duration_ms=retail2_ms,
                    llm_messages={
                        "system": retail2_state.get("system_prompt", ""),
                        "user": retail2_state.get("user_message", ""),
                    },
                )
            elif retail2_status == "err":
                retail2_exc = retail2_value
                retail2_result = {
                    "Display_id": offer_id,
                    "Classification": "UNCLASSIFIED",
                    "Confidence": "None",
                    "Reason": "Retail Agent 2 failed; see retail_agent_2_error.",
                    "error": str(retail2_exc),
                }
                trace.add_step("Retail Agent 2", "llm_agent",
                    error=retail2_exc,
                    duration_ms=retail2_ms,
                )
            if retail2_status == "skipped":
                retail2_result = {
                    "Display_id": offer_id, "Classification": "-", "Confidence": "-", "Reason": "Not selected",
                }

            # Step 8: ISQ Validation Agent
            isq_status, isq_value, isq_ms = isq_outcome
            if isq_status == "ok":
                isq_state = isq_value
                isq_result = isq_state["result"]
                trace.add_step("ISQ Validation Agent", "llm_agent",
                    input_=isq_state.get("agent_input", {}),
                    raw_output=isq_state.get("raw_output", ""),
                    parsed=isq_result,
                    duration_ms=isq_ms,
                    llm_messages={
                        "system": isq_state.get("system_prompt", ""),
                        "user": isq_state.get("user_message", ""),
                    },
                )
            elif isq_status == "err":
                isq_exc = isq_value
                isq_result = {
                    "status": "not_outlier",
                    "reason": "ISQ validation agent failed.",
                    "error": str(isq_exc),
                }
                trace.add_step("ISQ Validation Agent", "llm_agent",
                    error=isq_exc,
                    duration_ms=isq_ms,
                )
            if isq_status == "skipped":
                isq_result = {
                    "status": "-", "reason": "Not selected",
                }

            # Step 9: Description Agent
            desc_status, desc_value, desc_ms = desc_outcome
            if desc_status == "ok":
                desc_state = desc_value
                desc_result = desc_state["result"]
                trace.add_step("Description Agent", "llm_agent",
                    input_=desc_state.get("agent_input", {}),
                    raw_output=desc_state.get("raw_output", ""),
                    parsed=desc_result,
                    duration_ms=desc_ms,
                    llm_messages={
                        "system": desc_state.get("system_prompt", ""),
                        "user": desc_state.get("user_message", ""),
                    },
                )
            elif desc_status == "err":
                desc_exc = desc_value
                desc_result = {
                    "status": "error",
                    "reason": "Description agent failed; see desc_error.",
                    "error": str(desc_exc),
                }
                trace.add_step("Description Agent", "llm_agent",
                    error=desc_exc,
                    duration_ms=desc_ms,
                )
            if desc_status == "skipped":
                desc_result = {"status": "-", "reason": "Not selected"}

            # Step 10: Specs vs Category Agent 2
            svc_a2_status, svc_a2_value, svc_a2_ms = specs_vs_cat_a2_outcome
            if svc_a2_status == "ok":
                svc_a2_state = svc_a2_value
                specs_vs_cat_a2_result = svc_a2_state["result"]
                trace.add_step("Specs vs Category Agent 2", "llm_agent",
                    input_=svc_a2_state.get("agent_input", {}),
                    raw_output=svc_a2_state.get("raw_output", ""),
                    parsed=specs_vs_cat_a2_result,
                    duration_ms=svc_a2_ms,
                    llm_messages={
                        "system": svc_a2_state.get("system_prompt", ""),
                        "user": svc_a2_state.get("user_message", ""),
                    },
                )
            elif svc_a2_status == "err":
                svc_a2_exc = svc_a2_value
                specs_vs_cat_a2_result = {
                    "Display_id": offer_id, "Status": "Error", "Score": None,
                    "Confidence": "None", "Reason": "Specs vs Category Agent 2 failed; see error.",
                    "Issues": [], "error": str(svc_a2_exc),
                }
                trace.add_step("Specs vs Category Agent 2", "llm_agent",
                    error=svc_a2_exc, duration_ms=svc_a2_ms,
                )
            if svc_a2_status == "skipped":
                specs_vs_cat_a2_result = {
                    "Display_id": offer_id, "Status": "-", "Score": None,
                    "Confidence": "-", "Reason": "Not selected", "Issues": [],
                }

            # Step 11: Title vs Category Agent 2
            tvc_a2_status, tvc_a2_value, tvc_a2_ms = title_vs_cat_a2_outcome
            if tvc_a2_status == "ok":
                tvc_a2_state = tvc_a2_value
                title_vs_cat_a2_result = tvc_a2_state["result"]
                trace.add_step("Title vs Category Agent 2", "llm_agent",
                    input_=tvc_a2_state.get("agent_input", {}),
                    raw_output=tvc_a2_state.get("raw_output", ""),
                    parsed=title_vs_cat_a2_result,
                    duration_ms=tvc_a2_ms,
                    llm_messages={
                        "system": tvc_a2_state.get("system_prompt", ""),
                        "user": tvc_a2_state.get("user_message", ""),
                    },
                )
            elif tvc_a2_status == "err":
                tvc_a2_exc = tvc_a2_value
                title_vs_cat_a2_result = {
                    "Display_id": offer_id, "Status": "Error", "Score": None,
                    "Confidence": "None", "Reason": "Title vs Category Agent 2 failed; see error.",
                    "Issues": [], "error": str(tvc_a2_exc),
                }
                trace.add_step("Title vs Category Agent 2", "llm_agent",
                    error=tvc_a2_exc, duration_ms=tvc_a2_ms,
                )
            if tvc_a2_status == "skipped":
                title_vs_cat_a2_result = {
                    "Display_id": offer_id, "Status": "-", "Score": None,
                    "Confidence": "-", "Reason": "Not selected", "Issues": [],
                }

            # Step 12: Title vs Specs Agent 2
            tvs_a2_status, tvs_a2_value, tvs_a2_ms = title_vs_specs_a2_outcome
            if tvs_a2_status == "ok":
                tvs_a2_state = tvs_a2_value
                title_vs_specs_a2_result = tvs_a2_state["result"]
                trace.add_step("Title vs Specs Agent 2", "llm_agent",
                    input_=tvs_a2_state.get("agent_input", {}),
                    raw_output=tvs_a2_state.get("raw_output", ""),
                    parsed=title_vs_specs_a2_result,
                    duration_ms=tvs_a2_ms,
                    llm_messages={
                        "system": tvs_a2_state.get("system_prompt", ""),
                        "user": tvs_a2_state.get("user_message", ""),
                    },
                )
            elif tvs_a2_status == "err":
                tvs_a2_exc = tvs_a2_value
                title_vs_specs_a2_result = {
                    "Display_id": offer_id, "Status": "Error", "Score": None,
                    "Confidence": "None", "Reason": "Title vs Specs Agent 2 failed; see error.",
                    "Issues": [], "error": str(tvs_a2_exc),
                }
                trace.add_step("Title vs Specs Agent 2", "llm_agent",
                    error=tvs_a2_exc, duration_ms=tvs_a2_ms,
                )
            if tvs_a2_status == "skipped":
                title_vs_specs_a2_result = {
                    "Display_id": offer_id, "Status": "-", "Score": None,
                    "Confidence": "-", "Reason": "Not selected", "Issues": [],
                }

            # Step 12b: Buyer Profile Agent (standalone — not part of BL Score 1/2)
            buyer_profile_status, buyer_profile_value, buyer_profile_ms = buyer_profile_outcome
            if buyer_profile_status == "ok":
                buyer_profile_state = buyer_profile_value
                buyer_profile_result = buyer_profile_state["result"]
                for _c in buyer_profile_state.get("api_calls", []):
                    trace.add_step(_c.get("name", "Buyer Profile API"), "api_call",
                        endpoint=_c.get("endpoint", ""),
                        input_=_c.get("input"),
                        output=_c.get("output"),
                        error=(_c.get("error") or None),
                        duration_ms=_c.get("duration_ms", 0),
                    )
                trace.add_step("Buyer Profile Agent", "llm_agent",
                    input_=buyer_profile_state.get("agent_input", {}),
                    raw_output=buyer_profile_state.get("raw_output", ""),
                    parsed=buyer_profile_result,
                    duration_ms=buyer_profile_ms,
                    llm_messages={
                        "system": buyer_profile_state.get("system_prompt", ""),
                        "user": buyer_profile_state.get("user_message", ""),
                    },
                )
            elif buyer_profile_status == "err":
                buyer_profile_exc = buyer_profile_value
                buyer_profile_result = {
                    "Display_id": offer_id,
                    "status": "not related",
                    "reasoning": "Buyer Profile Agent failed; see error.",
                    "Profile_Status": "Incomplete",
                    "Profile_Check_Reason": "Buyer Profile Agent failed; see error.",
                    "Tenure": "Unknown",
                    "Glid": "",
                    "error": str(buyer_profile_exc),
                }
                trace.add_step("Buyer Profile Agent", "llm_agent",
                    error=buyer_profile_exc, duration_ms=buyer_profile_ms,
                )
            if buyer_profile_status == "skipped":
                buyer_profile_result = {
                    "Display_id": offer_id, "status": "not related", "reasoning": "Not selected",
                    "Profile_Status": "Incomplete", "Profile_Check_Reason": "Not selected",
                    "Tenure": "Unknown", "Glid": "",
                }

            # Step 12c: Recheck — second-opinion LLM call for flagged Windmill agents.
            # Fires only when exactly 1 or 2 of the 4 core agents return Wrong/Incorrect.
            def _wm_status(key: str) -> str:
                block = result.get(key) or {}
                return (block.get("status") or block.get("final_verdict") or "").lower()

            _recheck_flagged: list[str] = []
            _recheck_before: dict = {}
            if _wm_status("title_category_outlier") in ("wrong", "outlier"):
                _recheck_flagged.append("title_vs_category")
                _recheck_before["title_category_outlier"] = _wm_status("title_category_outlier")
            if _wm_status("title_spec_verdict") in ("wrong", "outlier"):
                _recheck_flagged.append("title_vs_specs")
                _recheck_before["title_spec_verdict"] = _wm_status("title_spec_verdict")
            if _wm_status("specs_category_outlier") in ("wrong", "outlier"):
                _recheck_flagged.append("specs_vs_category")
                _recheck_before["specs_category_outlier"] = _wm_status("specs_category_outlier")
            if isq_result.get("status", "") == "outlier":
                _recheck_flagged.append("isq_validation")
                _recheck_before["isq"] = isq_result.get("status", "")

            recheck_result: dict = {"ran": False, "results": []}
            if 1 <= len(_recheck_flagged) <= 2:
                try:
                    recheck_result = await run_recheck_agent(offer_id, buylead_response, _recheck_flagged)
                except Exception as _rc_exc:
                    recheck_result = {"ran": True, "error": str(_rc_exc), "results": []}
                    log.warning("recheck_agent raised unexpectedly: %s", _rc_exc)

                _rc_name_to_key = {v: k for k, v in _RECHECK_AGENT_NAMES.items()}
                for _rc_item in recheck_result.get("results", []):
                    if _rc_item.get("status") != "not_outlier":
                        continue
                    _rc_key = _rc_name_to_key.get(_rc_item.get("agent", ""))
                    _rc_reason = _rc_item.get("reason", "")
                    if _rc_key == "title_vs_category":
                        result = {**result, "title_category_outlier": {**result.get("title_category_outlier", {}), "status": "Correct", "reason": _rc_reason}}
                    elif _rc_key == "title_vs_specs":
                        result = {**result, "title_spec_verdict": {**result.get("title_spec_verdict", {}), "status": "Correct", "final_verdict": "Correct", "reason": _rc_reason}}
                    elif _rc_key == "specs_vs_category":
                        result = {**result, "specs_category_outlier": {**result.get("specs_category_outlier", {}), "status": "Correct", "reason": _rc_reason}}
                    elif _rc_key == "isq_validation":
                        isq_result = {**isq_result, "status": "not_outlier", "reason": _rc_reason}

            _rc_internal_to_before_key = {
                "title_vs_category": "title_category_outlier",
                "title_vs_specs":    "title_spec_verdict",
                "specs_vs_category": "specs_category_outlier",
                "isq_validation":    "isq",
            }
            _rc_name_to_key_r = {v: k for k, v in _RECHECK_AGENT_NAMES.items()}
            recheck_lookup: dict = {}
            for _rc_item in recheck_result.get("results", []):
                _enriched = dict(_rc_item)
                _internal = _rc_name_to_key_r.get(_rc_item.get("agent", ""), "")
                _enriched["before_status"] = _recheck_before.get(_rc_internal_to_before_key.get(_internal, ""), "")
                recheck_lookup[_rc_item["agent"]] = _enriched

            if recheck_result.get("ran"):
                trace.add_step(
                    "Recheck Agent", "llm_agent",
                    input_={"flagged_agents": _recheck_flagged, "before": _recheck_before},
                    raw_output=recheck_result.get("raw_output", ""),
                    parsed={"results": recheck_result.get("results", []), "error": recheck_result.get("error", "")},
                    duration_ms=0,
                )

            # Step 12d: Decision Agent (deterministic)
            try:
                decision_result = run_decision_agent(
                    windmill_result=result,
                    isq_result=isq_result,
                    recheck_flagged=_recheck_flagged,
                    recheck_results=recheck_result.get("results", []),
                )
            except Exception as _dec_exc:
                decision_result = {"Final_Verdict": "outlier", "Reason": f"Decision agent error: {_dec_exc}"}
            trace.add_step("Decision Agent", "transform", parsed=decision_result)

            # Step 13: BuyLead Score (deterministic, runs after all agents)
            try:
                scoring_result = run_scoring_agent(
                    audit_result=result,
                    isq_result=isq_result,
                    desc_result=desc_result,
                    retail2_result=retail2_result,
                )
            except Exception as _score_exc:
                scoring_result = {**_SCORING_FAIL, "error": str(_score_exc)}

            # Step 14: BuyLead Score 2 (Agent 2 category checks + shared agents)
            try:
                scoring_result_2 = run_scoring_agent2(
                    specs_vs_cat_a2_result=specs_vs_cat_a2_result,
                    title_vs_cat_a2_result=title_vs_cat_a2_result,
                    title_vs_specs_a2_result=title_vs_specs_a2_result,
                    isq_result=isq_result,
                    desc_result=desc_result,
                    retail2_result=retail2_result,
                )
            except Exception as _score2_exc:
                scoring_result_2 = {**_SCORING2_FAIL, "error": str(_score2_exc)}

            # Step 15: BL Completeness Score (deterministic)
            try:
                _bl_data = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {})
                completeness_result = run_completeness_agent(
                    bl_title=_bl_data.get("ETO_OFR_TITLE") or payload.get("item_name", ""),
                    mcat_name=payload.get("mcat_name", ""),
                    isq=payload.get("ISQ", []),
                    enrichmentinfo_raw=_bl_data.get("ENRICHMENTINFO"),
                    description=_bl_data.get("ETO_OFR_DESC"),
                )
            except Exception as _comp_exc:
                completeness_result = {**_COMPLETENESS_FAIL, "error": str(_comp_exc)}

            # Deterministic Quantity Audit (no LLM) — non-fatal.
            try:
                quantity_audit_result = run_quantity_agent(offer_id, buylead_response, user_detail=user_detail)
            except Exception as _qa_exc:
                quantity_audit_result = {"status": "Unverifiable", "reason": str(_qa_exc), "rules_fired": []}

            # Audit API failure is non-fatal — log it and continue with empty result.
            if audit_status == "err":
                log.warning("Windmill agents failed (non-fatal): %s", audit_value)

            # Step 16: desc-dependent activity-keyword enrichment (activity already
            # fetched pre-gather). Non-fatal.
            activity_kw_result, searched_terms_match = await _enrich_activity_keywords(
                buyer_activity, buylead_response, payload, desc_result, trace,
            )
            buyer_activity_result = buyer_activity
            activity_keywords = (activity_kw_result or {}).get("keywords") or []

            try:
                trace_id = trace.save(
                    item_name=result.get("item_name") or payload.get("item_name", ""),
                    mcat_name=payload.get("mcat_name", ""),
                    audit_done_by=actor,
                )
            except Exception:
                trace_id = None
            existing_retail_flag = extract_existing_retail_flag(buylead_response)
            csv_path = append_audit_dashboard_row(
                offer_id, payload, result, buyer_result,
                trace_id=trace_id,
                existing_retail_flag=existing_retail_flag,
                retail_agent_2_response=retail2_result,
                isq_validation_response=isq_result,
                description_response=desc_result,
                specs_vs_category_agent2_response=specs_vs_cat_a2_result,
                title_vs_category_agent2_response=title_vs_cat_a2_result,
                title_vs_specs_agent2_response=title_vs_specs_a2_result,
                scoring_response=scoring_result,
                scoring_response_2=scoring_result_2,
                completeness_response=completeness_result,
                activity_keywords=", ".join(activity_keywords),
                quantity_audit_response=quantity_audit_result,
                buyer_profile_response=buyer_profile_result,
                buyer_viewed_vs_csl_response=buyer_viewed_vs_csl_result,
                audit_done_by=actor,
                recheck_data={"ran": recheck_result.get("ran", False), "before": _recheck_before, "results": recheck_result.get("results", [])},
                decision_response=decision_result,
            )
        except Exception as _outer_exc:
            if fatal_exc is None:
                fatal_exc = _outer_exc
            if failed_step is None:
                failed_step = "Audit Pipeline"
            try:
                trace_id = trace.save(
                    item_name=(payload or {}).get("item_name", ""),
                    mcat_name=(payload or {}).get("mcat_name", ""),
                    audit_done_by=actor,
                )
            except Exception:
                trace_id = None
            return templates.TemplateResponse(
                request,
                "audit_error.html",
                {
                    "offer_id": offer_id,
                    "failed_step": failed_step or "Unknown",
                    "error_message": str(fatal_exc),
                    "friendly_error": _friendly_error(fatal_exc),
                    "steps": trace.steps,
                    "trace_id": trace_id,
                    "buylead_response": buylead_response,
                    "buylead_raw_json": json.dumps(buylead_response, indent=2, ensure_ascii=False) if buylead_response else "",
                    "payload": payload or {},
                    "payload_json": json.dumps(payload, indent=2, ensure_ascii=False) if payload else "",
                },
            )

        print(json.dumps({
            "event": "audit_response",
            "offer_id": offer_id,
            "bl_score_1": scoring_result.get("composite_score"),
            "bl_verdict_1": scoring_result.get("verdict"),
            "bl_score_2": scoring_result_2.get("composite_score"),
            "bl_verdict_2": scoring_result_2.get("verdict"),
            "retail": retail2_result.get("Classification"),
            "isq_status": isq_result.get("status") or isq_result.get("Status"),
            "desc_status": desc_result.get("status"),
            "completeness_score": completeness_result.get("total_score"),
            "decision": decision_result.get("Final_Verdict"),
            "audit_done_by": actor,
        }), flush=True)

        return templates.TemplateResponse(
            request,
            "result.html",
            {
                "data": result,
                "payload": payload,
                "offer_id": offer_id,
                "buylead_response": buylead_response,
                "buylead_raw_json": json.dumps(buylead_response, indent=2),
                "csv_path": csv_path,
                "existing_retail_flag": existing_retail_flag,
                "raw_json": json.dumps(result, indent=2),
                "buyer_result": buyer_result,
                "buyer_raw_json": json.dumps(buyer_result, indent=2, ensure_ascii=False),
                "retail_agent_2_result": retail2_result,
                "retail_agent_2_raw_json": json.dumps(retail2_result, indent=2, ensure_ascii=False),
                "isq_result": isq_result,
                "isq_raw_json": json.dumps(isq_result, indent=2, ensure_ascii=False),
                "desc_result": desc_result,
                "desc_raw_json": json.dumps(desc_result, indent=2, ensure_ascii=False),
                "specs_vs_cat_a2_result": specs_vs_cat_a2_result,
                "specs_vs_cat_a2_raw_json": json.dumps(specs_vs_cat_a2_result, indent=2, ensure_ascii=False),
                "title_vs_cat_a2_result": title_vs_cat_a2_result,
                "title_vs_cat_a2_raw_json": json.dumps(title_vs_cat_a2_result, indent=2, ensure_ascii=False),
                "title_vs_specs_a2_result": title_vs_specs_a2_result,
                "title_vs_specs_a2_raw_json": json.dumps(title_vs_specs_a2_result, indent=2, ensure_ascii=False),
                "scoring_result": scoring_result,
                "scoring_result_2": scoring_result_2,
                "completeness_result": completeness_result,
                "quantity_audit_result": quantity_audit_result,
                "buyer_profile_result": buyer_profile_result,
                "buyer_profile_raw_json": json.dumps(buyer_profile_result, indent=2, ensure_ascii=False),
                "trace_id": trace_id,
                "show_retail_1": show_retail_1,
                "buyer_activity": buyer_activity_result,
                "buyer_glid": buyer_glid,
                "activity_keywords": activity_keywords,
                "activity_kw_result": activity_kw_result,
                "searched_terms_match": searched_terms_match,
                "agents_ran": sorted(selected_agents) if selected_agents is not None else None,
                "recheck_result": recheck_result,
                "recheck_lookup": recheck_lookup,
                "recheck_before": _recheck_before,
                "buyer_viewed_vs_csl_result": buyer_viewed_vs_csl_result,
                "decision_result": decision_result,
            },
        )
    except Exception as _unhandled:
        # Last-resort catch: something failed inside the error-handler itself
        # (e.g. trace.save raised, or _friendly_error blew up).  Always return
        # audit_error.html so the browser sees HTML, not a 500 JSON blob.
        _u_msg = str(_unhandled)
        try:
            _u_tid = trace.save(
                item_name=(payload or {}).get("item_name", ""),
                mcat_name=(payload or {}).get("mcat_name", ""),
                audit_done_by=actor,
            )
        except Exception:
            _u_tid = None
        return templates.TemplateResponse(
            request,
            "audit_error.html",
            {
                "offer_id": offer_id,
                "failed_step": failed_step or "Audit Pipeline",
                "error_message": _u_msg,
                "friendly_error": _friendly_error(_unhandled),
                "steps": trace.steps,
                "trace_id": _u_tid,
                "buylead_response": buylead_response,
                "buylead_raw_json": json.dumps(buylead_response, indent=2, ensure_ascii=False) if buylead_response else "",
                "payload": payload or {},
                "payload_json": json.dumps(payload, indent=2, ensure_ascii=False) if payload else "",
            },
        )
    finally:
        reset_audit_user(_user_token)
        reset_langfuse_handler(_lf_token)
        if _lf_handler:
            try:
                _lf_handler.flush()
            except Exception:
                pass


@router.get("/traces", response_class=HTMLResponse)
async def traces_list(request: Request):
    return templates.TemplateResponse(request, "traces.html", {
        "traces": list_traces(),
    })


@router.get("/traces/{trace_id}", response_class=HTMLResponse)
async def trace_detail(request: Request, trace_id: str):
    trace = get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return templates.TemplateResponse(request, "trace_detail.html", {
        "trace": trace,
        "show_retail_1": False,
    })


_BUYER_FAIL = {
    "status": "not_outlier", "product_matched": "0/0",
    "reason": "Buyer viewed agent did not run for this trace.",
}
_RETAIL_AGENT_2_FAIL = {
    "Classification": "UNCLASSIFIED", "Confidence": "None",
    "Reason": "Retail Agent 2 did not run for this trace.",
}
_ISQ_FAIL = {
    "status": "not_outlier",
    "reason": "ISQ validation agent did not run for this trace.",
}
_DESC_FAIL = {
    "status": "error",
    "reason": "Description agent did not run for this trace.",
}
_SCORING_FAIL = {
    "composite_score": None, "verdict": "Error", "verdict_class": "na",
    "agent_breakdown": [], "na_agents": [], "redistributed_weight": 0,
}
_COMPLETENESS_FAIL = {
    "total_score": None, "percentage": None,
    "breakdown": {}, "error": "Completeness agent failed.",
}
_SCORING2_FAIL = {
    "composite_score": None, "verdict": "Error", "verdict_class": "na",
    "agent_breakdown": [], "na_agents": [], "redistributed_weight": 0,
}
_SPECS_VS_CAT_A2_FAIL = {
    "Status": "Unverifiable", "Score": None, "Confidence": "None",
    "Reason": "Specs vs Category Agent 2 did not run for this trace.",
    "Issues": [],
}
_TITLE_VS_CAT_A2_FAIL = {
    "Status": "Unverifiable", "Score": None, "Confidence": "None",
    "Reason": "Title vs Category Agent 2 did not run for this trace.",
    "Issues": [],
}
_TITLE_VS_SPECS_A2_FAIL = {
    "Status": "Unverifiable", "Score": None, "Confidence": "None",
    "Reason": "Title vs Specs Agent 2 did not run for this trace.",
    "Issues": [],
}
_BUYER_VIEWED_VS_CSL_FAIL = {
    "status": "not matched",
    "reason": "Buyer Viewed vs CSL Agent did not run for this trace.",
}
_BUYER_PROFILE_FAIL = {
    "status": "not related",
    "reasoning": "Buyer Profile Agent did not run for this trace.",
    "Profile_Status": "Incomplete",
    "Profile_Check_Reason": "Not available for this trace.",
    "Tenure": "Unknown",
    "Glid": "",
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
    return await _trace_detail_handler(request, trace_id, show_retail_1=False)


@router.get("/admin_view/traces/{trace_id}/detail", response_class=HTMLResponse)
async def admin_trace_detail_view(request: Request, trace_id: str):
    return await _trace_detail_handler(request, trace_id, show_retail_1=True)


async def _trace_detail_handler(request: Request, trace_id: str, show_retail_1: bool):
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
    buyer_step = steps_by_name.get("Buyer Viewed Agent", {})
    retail2_step = steps_by_name.get("Retail Agent 2", {})
    isq_step = steps_by_name.get("ISQ Validation Agent", {})
    desc_step = steps_by_name.get("Description Agent", {})
    svc_a2_step = steps_by_name.get("Specs vs Category Agent 2", {})
    tvc_a2_step = steps_by_name.get("Title vs Category Agent 2", {})
    tvs_a2_step = steps_by_name.get("Title vs Specs Agent 2", {})
    buyer_profile_step = steps_by_name.get("Buyer Profile Agent", {})
    buyer_viewed_vs_csl_step = steps_by_name.get("Buyer Viewed vs CSL", {})
    buyer_activity_step = steps_by_name.get("Buyer Activity", {})
    activity_kw_step = steps_by_name.get("Activity Keywords", {})
    searched_terms_step = steps_by_name.get("Searched Terms Match", {})

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
            request,
            "audit_error.html",
            {
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

    buyer_result = _agent_result_from_step(buyer_step, offer_id, _BUYER_FAIL)
    retail2_result = _agent_result_from_step(retail2_step, offer_id, _RETAIL_AGENT_2_FAIL)
    isq_result = _agent_result_from_step(isq_step, offer_id, _ISQ_FAIL)
    desc_result = _agent_result_from_step(desc_step, offer_id, _DESC_FAIL)
    specs_vs_cat_a2_result = _agent_result_from_step(svc_a2_step, offer_id, _SPECS_VS_CAT_A2_FAIL)
    title_vs_cat_a2_result = _agent_result_from_step(tvc_a2_step, offer_id, _TITLE_VS_CAT_A2_FAIL)
    title_vs_specs_a2_result = _agent_result_from_step(tvs_a2_step, offer_id, _TITLE_VS_SPECS_A2_FAIL)
    buyer_profile_result = _agent_result_from_step(buyer_profile_step, offer_id, _BUYER_PROFILE_FAIL)
    buyer_viewed_vs_csl_result = _agent_result_from_step(buyer_viewed_vs_csl_step, offer_id, _BUYER_VIEWED_VS_CSL_FAIL)

    # Reconstruct recheck from saved trace and re-apply mutations so detail view
    # shows post-recheck statuses, not the raw pre-recheck Audit API output.
    _rc_step = steps_by_name.get("Recheck Agent", {})
    _rc_input = _rc_step.get("input") if isinstance(_rc_step, dict) else {}
    _rc_parsed = _rc_step.get("parsed") if isinstance(_rc_step, dict) else {}
    _recheck_flagged = (_rc_input or {}).get("flagged_agents", [])
    _recheck_before = (_rc_input or {}).get("before", {})
    _rc_results = (_rc_parsed or {}).get("results", [])
    recheck_result = {"ran": bool(_rc_step and _rc_step.get("name")), "results": _rc_results}

    _rc_name_to_key = {v: k for k, v in _RECHECK_AGENT_NAMES.items()}
    for _rc_item in _rc_results:
        if _rc_item.get("status") != "not_outlier":
            continue
        _rc_key = _rc_name_to_key.get(_rc_item.get("agent", ""))
        _rc_reason = _rc_item.get("reason", "")
        if _rc_key == "title_vs_category":
            audit_result = {**audit_result, "title_category_outlier": {**audit_result.get("title_category_outlier", {}), "status": "Correct", "reason": _rc_reason}}
        elif _rc_key == "title_vs_specs":
            audit_result = {**audit_result, "title_spec_verdict": {**audit_result.get("title_spec_verdict", {}), "status": "Correct", "final_verdict": "Correct", "reason": _rc_reason}}
        elif _rc_key == "specs_vs_category":
            audit_result = {**audit_result, "specs_category_outlier": {**audit_result.get("specs_category_outlier", {}), "status": "Correct", "reason": _rc_reason}}
        elif _rc_key == "isq_validation":
            isq_result = {**isq_result, "status": "not_outlier", "reason": _rc_reason}

    _rc_internal_to_before_key = {
        "title_vs_category": "title_category_outlier",
        "title_vs_specs":    "title_spec_verdict",
        "specs_vs_category": "specs_category_outlier",
        "isq_validation":    "isq",
    }
    recheck_lookup: dict = {}
    for _rc_item in _rc_results:
        _enriched = dict(_rc_item)
        _internal = _rc_name_to_key.get(_rc_item.get("agent", ""), "")
        _enriched["before_status"] = _recheck_before.get(_rc_internal_to_before_key.get(_internal, ""), "")
        recheck_lookup[_rc_item["agent"]] = _enriched

    try:
        scoring_result = run_scoring_agent(
            audit_result=audit_result,
            isq_result=isq_result,
            desc_result=desc_result,
            retail2_result=retail2_result,
        )
    except Exception as _score_exc:
        scoring_result = {**_SCORING_FAIL, "error": str(_score_exc)}

    try:
        scoring_result_2 = run_scoring_agent2(
            specs_vs_cat_a2_result=specs_vs_cat_a2_result,
            title_vs_cat_a2_result=title_vs_cat_a2_result,
            title_vs_specs_a2_result=title_vs_specs_a2_result,
            isq_result=isq_result,
            desc_result=desc_result,
            retail2_result=retail2_result,
        )
    except Exception as _score2_exc:
        scoring_result_2 = {**_SCORING2_FAIL, "error": str(_score2_exc)}

    try:
        _bl_data2 = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {})
        completeness_result = run_completeness_agent(
            bl_title=_bl_data2.get("ETO_OFR_TITLE") or (payload or {}).get("item_name", ""),
            mcat_name=(payload or {}).get("mcat_name", ""),
            isq=(payload or {}).get("ISQ", []),
            enrichmentinfo_raw=_bl_data2.get("ENRICHMENTINFO"),
            description=_bl_data2.get("ETO_OFR_DESC"),
        )
    except Exception as _comp_exc:
        completeness_result = {**_COMPLETENESS_FAIL, "error": str(_comp_exc)}

    # Reconstruct the User Detail response from the saved trace so R6 (qty ==
    # buyer mobile/zip) matches the live audit on re-render.
    _ud_step = steps_by_name.get("User Detail API", {})
    _ud_out = _ud_step.get("output") if isinstance(_ud_step, dict) else None
    rerender_user_detail = _ud_out if isinstance(_ud_out, dict) else {}
    try:
        quantity_audit_result = run_quantity_agent(offer_id, buylead_response, user_detail=rerender_user_detail)
    except Exception as _qa_exc:
        quantity_audit_result = {"status": "Unverifiable", "reason": str(_qa_exc), "rules_fired": []}

    # Reconstruct buyer-activity / desc-card extras from saved trace steps so the
    # detail view matches the live audit. The raw activity response is stored on
    # the step; re-parse it. Keywords + searched-terms match are stored parsed.
    ba_input = buyer_activity_step.get("input") if isinstance(buyer_activity_step, dict) else {}
    buyer_glid = (ba_input or {}).get("glusrId") or (ba_input or {}).get("decrypted_glid") \
        or (buyer_profile_result or {}).get("Glid") or ""
    ba_raw = buyer_activity_step.get("output") if isinstance(buyer_activity_step.get("output"), dict) else None
    buyer_activity_result = None
    if ba_raw is not None:
        try:
            buyer_activity_result = parse_buyer_activity(ba_raw, glusr_id=buyer_glid)
        except Exception:
            buyer_activity_result = None
    activity_kw_result = activity_kw_step.get("parsed") if isinstance(activity_kw_step.get("parsed"), dict) else None
    activity_keywords = (activity_kw_result or {}).get("keywords") or []
    searched_terms_match = searched_terms_step.get("parsed") if isinstance(searched_terms_step.get("parsed"), dict) else None

    # Reconstruct Decision Agent result from saved trace step.
    _dec_step = steps_by_name.get("Decision Agent", {})
    _dec_parsed = _dec_step.get("parsed") if isinstance(_dec_step.get("parsed"), dict) else None
    if _dec_parsed:
        decision_result = _dec_parsed
    else:
        try:
            decision_result = run_decision_agent(
                windmill_result=audit_result,
                isq_result=isq_result,
                recheck_flagged=_recheck_flagged,
                recheck_results=_rc_results,
            )
        except Exception:
            decision_result = {"Final_Verdict": "not_outlier", "Reason": ""}

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
        request,
        "result.html",
        {
            "data": audit_result,
            "payload": payload,
            "offer_id": offer_id,
            "buylead_response": buylead_response,
            "csv_path": "",
            "existing_retail_flag": extract_existing_retail_flag(buylead_response),
            "buyer_result": buyer_result,
            "retail_agent_2_result": retail2_result,
            "isq_result": isq_result,
            "desc_result": desc_result,
            "specs_vs_cat_a2_result": specs_vs_cat_a2_result,
            "title_vs_cat_a2_result": title_vs_cat_a2_result,
            "title_vs_specs_a2_result": title_vs_specs_a2_result,
            "trace_id": trace_id,
            "is_detail_view": True,
            "buylead_raw_json": _dump(buylead_response),
            "raw_json": _dump(audit_result),
            "buyer_raw_json": _dump(buyer_result),
            "retail_agent_2_raw_json": _dump(retail2_result),
            "isq_raw_json": _dump(isq_result),
            "desc_raw_json": _dump(desc_result),
            "specs_vs_cat_a2_raw_json": _dump(specs_vs_cat_a2_result),
            "title_vs_cat_a2_raw_json": _dump(title_vs_cat_a2_result),
            "title_vs_specs_a2_raw_json": _dump(title_vs_specs_a2_result),
            "scoring_result": scoring_result,
            "scoring_result_2": scoring_result_2,
            "completeness_result": completeness_result,
            "quantity_audit_result": quantity_audit_result,
            "buyer_profile_result": buyer_profile_result,
            "buyer_profile_raw_json": _dump(buyer_profile_result),
            "show_retail_1": show_retail_1,
            "buyer_activity": buyer_activity_result,
            "buyer_glid": buyer_glid,
            "activity_keywords": activity_keywords,
            "activity_kw_result": activity_kw_result,
            "searched_terms_match": searched_terms_match,
            "recheck_result": recheck_result,
            "recheck_lookup": recheck_lookup,
            "recheck_before": _recheck_before,
            "buyer_viewed_vs_csl_result": buyer_viewed_vs_csl_result,
            "decision_result": decision_result,
        },
    )


@router.get("/batch", response_class=HTMLResponse)
async def batch_page(request: Request):
    return templates.TemplateResponse(request, "batch.html", {
        "show_retail_1": False,
        "home_url": "/",
        "records_url": "/records",
        "trace_detail_base": "/traces",
    })


@router.get("/batch/stream")
async def batch_stream(request: Request, offer_ids: str = "", selected_agents: str = ""):
    ids = [oid.strip() for oid in offer_ids.split(",") if oid.strip()]
    total = len(ids)
    actor = _audit_actor(request)
    _batch_selected = set(s.strip() for s in selected_agents.split(",") if s.strip()) if selected_agents else None

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

                # Validate BuyLead DATA — null means offer doesn't exist or was deleted
                _raw_data_b = buylead_response.get("RESPONSE", {}).get("DATA")
                if not isinstance(_raw_data_b, dict):
                    failed_step = "BuyLead API"
                    _result_code_b = (buylead_response.get("RESPONSE") or {}).get("RESULT", "")
                    raise ValueError(
                        f"BuyLead returned no DATA for offer_id={offer_id} — "
                        f"offer may not exist (RESULT={_result_code_b!r})"
                    )

                try:
                    payload = build_audit_payload_from_buylead(offer_id, buylead_response)
                    trace.add_step("Payload Build", "transform", output=payload)
                except Exception as exc:
                    trace.add_step("Payload Build", "transform", error=exc)
                    failed_step = "Payload Build"
                    raise

                # Steps 3–5 run concurrently: Audit API + Buyer + Retail Agent 2 + remaining agents.
                async def _timed(coro):
                    t = time.monotonic()
                    try:
                        return ("ok", await coro, int((time.monotonic() - t) * 1000))
                    except Exception as e:
                        return ("err", e, int((time.monotonic() - t) * 1000))

                async def _noop():
                    return ("skipped", None, 0)

                def _pick(key, coro_fn):
                    return _timed(coro_fn()) if (_batch_selected is None or key in _batch_selected) else _noop()

                # User Detail — non-fatal; empty dict lets downstream agents degrade gracefully.
                try:
                    user_detail = await fetch_user_detail_for_buylead(buylead_response)
                except Exception as _ud_exc:
                    log.warning("batch: fetch_user_detail failed for %s: %s", offer_id, _ud_exc)
                    user_detail = {}

                buyer_activity, buyer_glid = await _fetch_buyer_activity_for_bl(buylead_response, trace)

                # Buyer Viewed vs CSL Agent — deterministic, no LLM.
                if _batch_selected is None or "buyer_viewed_vs_csl" in _batch_selected:
                    _t_bvvc_batch = time.monotonic()
                    try:
                        batch_buyer_viewed_vs_csl_result = run_buyer_viewed_vs_csl(offer_id, buylead_response, buyer_activity)
                    except Exception as _bvvc_exc:
                        log.warning("batch: buyer_viewed_vs_csl failed for %s: %s", offer_id, _bvvc_exc)
                        batch_buyer_viewed_vs_csl_result = {"status": "error", "reason": str(_bvvc_exc), "error": str(_bvvc_exc)}
                    trace.add_step("Buyer Viewed vs CSL", "transform",
                        parsed=batch_buyer_viewed_vs_csl_result,
                        duration_ms=int((time.monotonic() - _t_bvvc_batch) * 1000),
                    )
                else:
                    batch_buyer_viewed_vs_csl_result = {"status": "not matched", "reason": "Not selected"}

                try:
                    _batch_outcomes = await asyncio.wait_for(
                        asyncio.gather(
                            _pick("auditmate",         lambda: call_windmill_agents(payload)),
                            _pick("buyer_viewed",      lambda: run_buyer_viewed_agent(offer_id, buylead_response, _trace=True)),
                            _pick("retail_agent_2",    lambda: run_retail_agent_2(offer_id, buylead_response, _trace=True)),
                            _pick("isq",               lambda: run_isq_validation_agent(offer_id, buylead_response, user_detail=user_detail, _trace=True)),
                            _pick("description",       lambda: run_description_agent(offer_id, buylead_response, _trace=True)),
                            _pick("specs_vs_category", lambda: run_specs_vs_category_agent2(offer_id, buylead_response, _trace=True)),
                            _pick("title_vs_category", lambda: run_title_vs_category_agent2(offer_id, buylead_response, _trace=True)),
                            _pick("title_vs_specs",    lambda: run_title_vs_specs_agent2(offer_id, buylead_response, _trace=True)),
                            _pick("buyer_profile",     lambda: run_buyer_profile_agent(offer_id, buylead_response, buyer_activity=buyer_activity, user_detail=user_detail, _trace=True)),
                        ),
                        timeout=_BATCH_OFFER_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    raise RuntimeError(
                        f"Agent processing timed out after {_BATCH_OFFER_TIMEOUT:.0f}s — offer skipped"
                    )
                (
                    audit_outcome, buyer_outcome, retail2_outcome,
                    isq_outcome, desc_outcome,
                    specs_vs_cat_a2_outcome, title_vs_cat_a2_outcome, title_vs_specs_a2_outcome,
                    buyer_profile_outcome,
                ) = _batch_outcomes

                # Step 3: Audit API
                audit_status, audit_value, audit_ms = audit_outcome
                if audit_status == "ok":
                    result = audit_value
                    trace.add_step("Audit API", "api_call",
                        endpoint=AUDITOR_API_URL,
                        input_=payload,
                        output=result,
                        duration_ms=audit_ms,
                    )
                elif audit_status == "skipped":
                    result = {}
                else:
                    _err_msg = str(audit_value)[:300]
                    result = {
                        "title_category_outlier": {"status": "Error", "reason": _err_msg},
                        "title_spec_verdict":     {"status": "Error", "reason": _err_msg},
                        "specs_category_outlier": {"status": "Error", "reason": _err_msg},
                    }
                    trace.add_step("Audit API", "api_call",
                        endpoint=AUDITOR_API_URL,
                        input_=payload,
                        error=audit_value,
                        duration_ms=audit_ms,
                    )

                # Step 4: Buyer Viewed Agent
                buyer_status, buyer_value, buyer_ms = buyer_outcome
                if buyer_status == "ok":
                    buyer_state = buyer_value
                    buyer_result = buyer_state["result"]
                    trace.add_step("Buyer Viewed Agent", "llm_agent",
                        input_=buyer_state.get("agent_input", {}),
                        raw_output=buyer_state.get("raw_output", ""),
                        parsed=buyer_result,
                        duration_ms=buyer_ms,
                        llm_messages={
                            "system": buyer_state.get("system_prompt", ""),
                            "user": buyer_state.get("user_message", ""),
                        },
                    )
                elif buyer_status == "err":
                    buyer_exc = buyer_value
                    buyer_result = {
                        "status": "not_outlier",
                        "reason": "Buyer viewed classification failed; see buyer_viewed_error.",
                        "product_matched": "0/0",
                        "error": str(buyer_exc),
                    }
                    trace.add_step("Buyer Viewed Agent", "llm_agent",
                        error=buyer_exc,
                        duration_ms=buyer_ms,
                    )
                if buyer_status == "skipped":
                    buyer_result = {
                        "status": "not_outlier", "reason": "Not selected", "product_matched": "0/0",
                    }

                # Step 5: Retail Agent 2
                retail2_status, retail2_value, retail2_ms = retail2_outcome
                if retail2_status == "ok":
                    retail2_state = retail2_value
                    retail2_result = retail2_state["result"]
                    trace.add_step("Retail Agent 2", "llm_agent",
                        input_=retail2_state.get("agent_input", {}),
                        raw_output=retail2_state.get("raw_output", ""),
                        parsed=retail2_result,
                        duration_ms=retail2_ms,
                        llm_messages={
                            "system": retail2_state.get("system_prompt", ""),
                            "user": retail2_state.get("user_message", ""),
                        },
                    )
                elif retail2_status == "err":
                    retail2_exc = retail2_value
                    retail2_result = {
                        "Display_id": offer_id,
                        "Classification": "UNCLASSIFIED",
                        "Confidence": "None",
                        "Reason": "Retail Agent 2 failed; see retail_agent_2_error.",
                        "error": str(retail2_exc),
                    }
                    trace.add_step("Retail Agent 2", "llm_agent",
                        error=retail2_exc,
                        duration_ms=retail2_ms,
                    )
                if retail2_status == "skipped":
                    retail2_result = {
                        "Display_id": offer_id, "Classification": "-", "Confidence": "-", "Reason": "Not selected",
                    }

                # Step 8: ISQ Validation Agent
                isq_status, isq_value, isq_ms = isq_outcome
                if isq_status == "ok":
                    isq_state = isq_value
                    isq_result = isq_state["result"]
                    trace.add_step("ISQ Validation Agent", "llm_agent",
                        input_=isq_state.get("agent_input", {}),
                        raw_output=isq_state.get("raw_output", ""),
                        parsed=isq_result,
                        duration_ms=isq_ms,
                        llm_messages={
                            "system": isq_state.get("system_prompt", ""),
                            "user": isq_state.get("user_message", ""),
                        },
                    )
                elif isq_status == "err":
                    isq_exc = isq_value
                    isq_result = {
                        "status": "not_outlier",
                        "reason": "ISQ validation agent failed.",
                        "error": str(isq_exc),
                    }
                    trace.add_step("ISQ Validation Agent", "llm_agent",
                        error=isq_exc,
                        duration_ms=isq_ms,
                    )
                if isq_status == "skipped":
                    isq_result = {
                        "status": "-", "reason": "Not selected",
                    }

                # Step 9: Description Agent
                desc_status, desc_value, desc_ms = desc_outcome
                if desc_status == "ok":
                    desc_state = desc_value
                    desc_result = desc_state["result"]
                    trace.add_step("Description Agent", "llm_agent",
                        input_=desc_state.get("agent_input", {}),
                        raw_output=desc_state.get("raw_output", ""),
                        parsed=desc_result,
                        duration_ms=desc_ms,
                        llm_messages={
                            "system": desc_state.get("system_prompt", ""),
                            "user": desc_state.get("user_message", ""),
                        },
                    )
                elif desc_status == "err":
                    desc_exc = desc_value
                    desc_result = {
                        "status": "error",
                        "reason": "Description agent failed; see desc_error.",
                        "error": str(desc_exc),
                    }
                    trace.add_step("Description Agent", "llm_agent",
                        error=desc_exc,
                        duration_ms=desc_ms,
                    )
                if desc_status == "skipped":
                    desc_result = {"status": "-", "reason": "Not selected"}

                # Step 10: Specs vs Category Agent 2
                svc_a2_status, svc_a2_value, svc_a2_ms = specs_vs_cat_a2_outcome
                if svc_a2_status == "ok":
                    svc_a2_state = svc_a2_value
                    specs_vs_cat_a2_result = svc_a2_state["result"]
                    trace.add_step("Specs vs Category Agent 2", "llm_agent",
                        input_=svc_a2_state.get("agent_input", {}),
                        raw_output=svc_a2_state.get("raw_output", ""),
                        parsed=specs_vs_cat_a2_result,
                        duration_ms=svc_a2_ms,
                        llm_messages={
                            "system": svc_a2_state.get("system_prompt", ""),
                            "user": svc_a2_state.get("user_message", ""),
                        },
                    )
                elif svc_a2_status == "err":
                    specs_vs_cat_a2_result = {
                        "Display_id": offer_id, "Status": "Error", "Score": None,
                        "Confidence": "None", "Reason": "Specs vs Category Agent 2 failed.",
                        "Issues": [], "error": str(svc_a2_value),
                    }
                    trace.add_step("Specs vs Category Agent 2", "llm_agent",
                        error=svc_a2_value, duration_ms=svc_a2_ms,
                    )
                if svc_a2_status == "skipped":
                    specs_vs_cat_a2_result = {
                        "Display_id": offer_id, "Status": "-", "Score": None,
                        "Confidence": "-", "Reason": "Not selected", "Issues": [],
                    }

                # Step 11: Title vs Category Agent 2
                tvc_a2_status, tvc_a2_value, tvc_a2_ms = title_vs_cat_a2_outcome
                if tvc_a2_status == "ok":
                    tvc_a2_state = tvc_a2_value
                    title_vs_cat_a2_result = tvc_a2_state["result"]
                    trace.add_step("Title vs Category Agent 2", "llm_agent",
                        input_=tvc_a2_state.get("agent_input", {}),
                        raw_output=tvc_a2_state.get("raw_output", ""),
                        parsed=title_vs_cat_a2_result,
                        duration_ms=tvc_a2_ms,
                        llm_messages={
                            "system": tvc_a2_state.get("system_prompt", ""),
                            "user": tvc_a2_state.get("user_message", ""),
                        },
                    )
                elif tvc_a2_status == "err":
                    title_vs_cat_a2_result = {
                        "Display_id": offer_id, "Status": "Error", "Score": None,
                        "Confidence": "None", "Reason": "Title vs Category Agent 2 failed.",
                        "Issues": [], "error": str(tvc_a2_value),
                    }
                    trace.add_step("Title vs Category Agent 2", "llm_agent",
                        error=tvc_a2_value, duration_ms=tvc_a2_ms,
                    )
                if tvc_a2_status == "skipped":
                    title_vs_cat_a2_result = {
                        "Display_id": offer_id, "Status": "-", "Score": None,
                        "Confidence": "-", "Reason": "Not selected", "Issues": [],
                    }

                # Step 12: Title vs Specs Agent 2
                tvs_a2_status, tvs_a2_value, tvs_a2_ms = title_vs_specs_a2_outcome
                if tvs_a2_status == "ok":
                    tvs_a2_state = tvs_a2_value
                    title_vs_specs_a2_result = tvs_a2_state["result"]
                    trace.add_step("Title vs Specs Agent 2", "llm_agent",
                        input_=tvs_a2_state.get("agent_input", {}),
                        raw_output=tvs_a2_state.get("raw_output", ""),
                        parsed=title_vs_specs_a2_result,
                        duration_ms=tvs_a2_ms,
                        llm_messages={
                            "system": tvs_a2_state.get("system_prompt", ""),
                            "user": tvs_a2_state.get("user_message", ""),
                        },
                    )
                elif tvs_a2_status == "err":
                    title_vs_specs_a2_result = {
                        "Display_id": offer_id, "Status": "Error", "Score": None,
                        "Confidence": "None", "Reason": "Title vs Specs Agent 2 failed.",
                        "Issues": [], "error": str(tvs_a2_value),
                    }
                    trace.add_step("Title vs Specs Agent 2", "llm_agent",
                        error=tvs_a2_value, duration_ms=tvs_a2_ms,
                    )
                if tvs_a2_status == "skipped":
                    title_vs_specs_a2_result = {
                        "Display_id": offer_id, "Status": "-", "Score": None,
                        "Confidence": "-", "Reason": "Not selected", "Issues": [],
                    }

                # Step 12b: Buyer Profile Agent (standalone — not part of BL Score 1/2)
                buyer_profile_status, buyer_profile_value, buyer_profile_ms = buyer_profile_outcome
                if buyer_profile_status == "ok":
                    buyer_profile_result = buyer_profile_value["result"]
                    for _c in buyer_profile_value.get("api_calls", []):
                        trace.add_step(_c.get("name", "Buyer Profile API"), "api_call",
                            endpoint=_c.get("endpoint", ""),
                            input_=_c.get("input"),
                            output=_c.get("output"),
                            error=(_c.get("error") or None),
                            duration_ms=_c.get("duration_ms", 0),
                        )
                    trace.add_step("Buyer Profile Agent", "llm_agent",
                        input_=buyer_profile_value.get("agent_input", {}),
                        raw_output=buyer_profile_value.get("raw_output", ""),
                        parsed=buyer_profile_result,
                        duration_ms=buyer_profile_ms,
                        llm_messages={
                            "system": buyer_profile_value.get("system_prompt", ""),
                            "user": buyer_profile_value.get("user_message", ""),
                        },
                    )
                elif buyer_profile_status == "err":
                    buyer_profile_result = {
                        "Display_id": offer_id,
                        "status": "not related",
                        "reasoning": "Buyer Profile Agent failed; see error.",
                        "Profile_Status": "Incomplete",
                        "Profile_Check_Reason": "Buyer Profile Agent failed; see error.",
                        "Tenure": "Unknown",
                        "Glid": "", "error": str(buyer_profile_value),
                    }
                    trace.add_step("Buyer Profile Agent", "llm_agent",
                        error=buyer_profile_value, duration_ms=buyer_profile_ms,
                    )
                if buyer_profile_status == "skipped":
                    buyer_profile_result = {
                        "Display_id": offer_id, "status": "not related", "reasoning": "Not selected",
                        "Profile_Status": "Incomplete", "Profile_Check_Reason": "Not selected",
                        "Tenure": "Unknown", "Glid": "",
                    }

                # Audit API failure is non-fatal — log it and continue with empty result.
                if audit_status == "err":
                    log.warning("Windmill agents failed (non-fatal): %s", audit_value)

                # desc-dependent activity-keyword enrichment (activity already fetched
                # pre-gather). Non-fatal.
                batch_activity_kw, _ = await _enrich_activity_keywords(
                    buyer_activity, buylead_response, payload, desc_result, trace,
                )
                batch_activity_keywords = (batch_activity_kw or {}).get("keywords") or []

                try:
                    ok_trace_id = trace.save(
                        item_name=result.get("item_name") or payload.get("item_name", ""),
                        mcat_name=payload.get("mcat_name", ""),
                        audit_done_by=actor,
                    )
                except Exception:
                    ok_trace_id = None

                existing_retail_flag = extract_existing_retail_flag(buylead_response)
                try:
                    batch_scoring = run_scoring_agent(
                        audit_result=result,
                        isq_result=isq_result,
                        desc_result=desc_result,
                        retail2_result=retail2_result,
                    )
                except Exception:
                    batch_scoring = {**_SCORING_FAIL}
                try:
                    batch_scoring_2 = run_scoring_agent2(
                        specs_vs_cat_a2_result=specs_vs_cat_a2_result,
                        title_vs_cat_a2_result=title_vs_cat_a2_result,
                        title_vs_specs_a2_result=title_vs_specs_a2_result,
                        isq_result=isq_result,
                        desc_result=desc_result,
                        retail2_result=retail2_result,
                    )
                except Exception:
                    batch_scoring_2 = {**_SCORING2_FAIL}

                try:
                    _bl_data_batch = (buylead_response or {}).get("RESPONSE", {}).get("DATA", {})
                    batch_completeness = run_completeness_agent(
                        bl_title=_bl_data_batch.get("ETO_OFR_TITLE") or payload.get("item_name", ""),
                        mcat_name=payload.get("mcat_name", ""),
                        isq=payload.get("ISQ", []),
                        enrichmentinfo_raw=_bl_data_batch.get("ENRICHMENTINFO"),
                        description=_bl_data_batch.get("ETO_OFR_DESC"),
                    )
                except Exception:
                    batch_completeness = {**_COMPLETENESS_FAIL}

                try:
                    batch_quantity_audit = run_quantity_agent(offer_id, buylead_response, user_detail=user_detail)
                except Exception as _qa_exc:
                    batch_quantity_audit = {"status": "Unverifiable", "reason": str(_qa_exc), "rules_fired": []}

                # Recheck — mirrors _audit_handler Step 12c
                def _batch_wm_status(key: str) -> str:
                    block = result.get(key) or {}
                    return (block.get("status") or block.get("final_verdict") or "").lower()

                _batch_recheck_flagged: list[str] = []
                _batch_recheck_before: dict = {}
                if _batch_wm_status("title_category_outlier") in ("wrong", "outlier"):
                    _batch_recheck_flagged.append("title_vs_category")
                    _batch_recheck_before["title_category_outlier"] = _batch_wm_status("title_category_outlier")
                if _batch_wm_status("title_spec_verdict") in ("wrong", "outlier"):
                    _batch_recheck_flagged.append("title_vs_specs")
                    _batch_recheck_before["title_spec_verdict"] = _batch_wm_status("title_spec_verdict")
                if _batch_wm_status("specs_category_outlier") in ("wrong", "outlier"):
                    _batch_recheck_flagged.append("specs_vs_category")
                    _batch_recheck_before["specs_category_outlier"] = _batch_wm_status("specs_category_outlier")
                if (isq_result.get("Status") or isq_result.get("status") or "").lower() == "incorrect":
                    _batch_recheck_flagged.append("isq_validation")
                    _batch_recheck_before["isq"] = isq_result.get("Status") or isq_result.get("status") or ""

                batch_recheck_result: dict = {"ran": False, "results": []}
                if 1 <= len(_batch_recheck_flagged) <= 2:
                    try:
                        batch_recheck_result = await run_recheck_agent(offer_id, buylead_response, _batch_recheck_flagged)
                    except Exception as _brc_exc:
                        batch_recheck_result = {"ran": True, "error": str(_brc_exc), "results": []}
                        log.warning("batch recheck_agent raised: %s", _brc_exc)

                    _brc_name_to_key = {v: k for k, v in _RECHECK_AGENT_NAMES.items()}
                    for _brc_item in batch_recheck_result.get("results", []):
                        if _brc_item.get("status") != "not_outlier":
                            continue
                        _brc_key = _brc_name_to_key.get(_brc_item.get("agent", ""))
                        _brc_reason = _brc_item.get("reason", "")
                        if _brc_key == "title_vs_category":
                            result = {**result, "title_category_outlier": {**result.get("title_category_outlier", {}), "status": "Correct", "reason": _brc_reason}}
                        elif _brc_key == "title_vs_specs":
                            result = {**result, "title_spec_verdict": {**result.get("title_spec_verdict", {}), "status": "Correct", "final_verdict": "Correct", "reason": _brc_reason}}
                        elif _brc_key == "specs_vs_category":
                            result = {**result, "specs_category_outlier": {**result.get("specs_category_outlier", {}), "status": "Correct", "reason": _brc_reason}}
                        elif _brc_key == "isq_validation":
                            isq_result = {**isq_result, "Status": "Correct", "Reason": _brc_reason}

                if batch_recheck_result.get("ran"):
                    trace.add_step(
                        "Recheck Agent", "llm_agent",
                        input_={"flagged_agents": _batch_recheck_flagged, "before": _batch_recheck_before},
                        raw_output=batch_recheck_result.get("raw_output", ""),
                        parsed={"results": batch_recheck_result.get("results", []), "error": batch_recheck_result.get("error", "")},
                        duration_ms=0,
                    )

                try:
                    batch_decision_result = run_decision_agent(
                        windmill_result=result,
                        isq_result=isq_result,
                        recheck_flagged=_batch_recheck_flagged,
                        recheck_results=batch_recheck_result.get("results", []),
                    )
                except Exception as _bdec_exc:
                    batch_decision_result = {"Final_Verdict": "not_outlier", "Reason": f"Decision agent error: {_bdec_exc}"}
                trace.add_step("Decision Agent", "transform", parsed=batch_decision_result)

                try:
                    append_audit_dashboard_row(
                        offer_id, payload, result, buyer_result,
                        trace_id=ok_trace_id,
                        existing_retail_flag=existing_retail_flag,
                        retail_agent_2_response=retail2_result,
                        isq_validation_response=isq_result,
                        description_response=desc_result,
                        specs_vs_category_agent2_response=specs_vs_cat_a2_result,
                        title_vs_category_agent2_response=title_vs_cat_a2_result,
                        title_vs_specs_agent2_response=title_vs_specs_a2_result,
                        scoring_response=batch_scoring,
                        scoring_response_2=batch_scoring_2,
                        completeness_response=batch_completeness,
                        activity_keywords=", ".join(batch_activity_keywords),
                        quantity_audit_response=batch_quantity_audit,
                        buyer_profile_response=buyer_profile_result,
                        buyer_viewed_vs_csl_response=batch_buyer_viewed_vs_csl_result,
                        audit_done_by=actor,
                        recheck_data={"ran": batch_recheck_result.get("ran", False), "before": _batch_recheck_before, "results": batch_recheck_result.get("results", [])},
                        decision_response=batch_decision_result,
                    )
                except Exception as _csv_exc:
                    log.warning("batch: CSV append failed for %s: %s", offer_id, _csv_exc)

                print(json.dumps({
                    "event": "audit_response",
                    "offer_id": offer_id,
                    "bl_score_1": batch_scoring.get("composite_score"),
                    "bl_verdict_1": batch_scoring.get("verdict"),
                    "bl_score_2": batch_scoring_2.get("composite_score"),
                    "bl_verdict_2": batch_scoring_2.get("verdict"),
                    "retail": retail2_result.get("Classification"),
                    "isq_status": isq_result.get("status") or isq_result.get("Status"),
                    "desc_status": desc_result.get("status"),
                    "completeness_score": batch_completeness.get("total_score"),
                    "decision": batch_decision_result.get("Final_Verdict"),
                    "audit_done_by": actor,
                }), flush=True)

                def _nested(src, *keys):
                    v = src
                    for k in keys:
                        v = v.get(k, "") if isinstance(v, dict) else ""
                    return v or ""

                _batch_rc_by_agent = {
                    r["agent"]: r
                    for r in batch_recheck_result.get("results", [])
                    if isinstance(r, dict) and r.get("agent")
                }

                yield _sse({
                    "index": i,
                    "total": total,
                    "offer_id": offer_id,
                    "trace_id": ok_trace_id,
                    "item_name": result.get("item_name") or payload.get("item_name", ""),
                    "item_desc": payload.get("item_desc", "") or "",
                    "mcat_name": payload.get("mcat_name", ""),
                    "price": payload.get("price", ""),
                    "existing_retail_flag": existing_retail_flag,
                    "specs_category_outlier_status": _nested(result, "specs_category_outlier", "status"),
                    "specs_category_outlier_reason": _nested(result, "specs_category_outlier", "reason"),
                    "title_category_outlier_status": _nested(result, "title_category_outlier", "status"),
                    "title_category_outlier_reason": _nested(result, "title_category_outlier", "reason"),
                    "title_spec_verdict": _nested(result, "title_spec_verdict", "status"),
                    "title_spec_verdict_reason": _nested(result, "title_spec_verdict", "reason"),
                    "buyer_viewed_status": buyer_result.get("status", ""),
                    "buyer_viewed_reason": buyer_result.get("reason", ""),
                    "buyer_viewed_product_matched": buyer_result.get("product_matched", ""),
                    "buyer_viewed_error": buyer_result.get("error", ""),
                    "retail_agent_2_classification": retail2_result.get("Classification", ""),
                    "retail_agent_2_confidence": retail2_result.get("Confidence", ""),
                    "retail_agent_2_reason": retail2_result.get("Reason", ""),
                    "retail_agent_2_error": retail2_result.get("error", ""),
                    "isq_status": isq_result.get("Status", ""),
                    "isq_score": isq_result.get("Score", "") if isq_result.get("Score") is not None else "",
                    "isq_confidence": isq_result.get("Confidence", ""),
                    "isq_reason": isq_result.get("Reason", ""),
                    "isq_error": isq_result.get("error", ""),
                    "desc_status": desc_result.get("status", ""),
                    "desc_reason": desc_result.get("reason", ""),
                    "desc_error": desc_result.get("error", ""),
                    "activity_keywords": ", ".join(batch_activity_keywords),
                    "specs_vs_cat_a2_status": specs_vs_cat_a2_result.get("Status", "") or specs_vs_cat_a2_result.get("status", ""),
                    "specs_vs_cat_a2_score": specs_vs_cat_a2_result.get("Score", specs_vs_cat_a2_result.get("score", "")),
                    "specs_vs_cat_a2_confidence": specs_vs_cat_a2_result.get("Confidence", "") or specs_vs_cat_a2_result.get("confidence", ""),
                    "specs_vs_cat_a2_reason": specs_vs_cat_a2_result.get("Reason", "") or specs_vs_cat_a2_result.get("reasoning", ""),
                    "specs_vs_cat_a2_error": specs_vs_cat_a2_result.get("error", ""),
                    "title_vs_cat_a2_status": title_vs_cat_a2_result.get("Status", "") or title_vs_cat_a2_result.get("status", ""),
                    "title_vs_cat_a2_score": title_vs_cat_a2_result.get("Score", title_vs_cat_a2_result.get("score", "")),
                    "title_vs_cat_a2_confidence": title_vs_cat_a2_result.get("Confidence", "") or title_vs_cat_a2_result.get("confidence", ""),
                    "title_vs_cat_a2_reason": title_vs_cat_a2_result.get("Reason", "") or title_vs_cat_a2_result.get("reasoning", ""),
                    "title_vs_cat_a2_error": title_vs_cat_a2_result.get("error", ""),
                    "title_vs_specs_a2_status": title_vs_specs_a2_result.get("Status", "") or title_vs_specs_a2_result.get("status", ""),
                    "title_vs_specs_a2_score": title_vs_specs_a2_result.get("Score", title_vs_specs_a2_result.get("score", "")),
                    "title_vs_specs_a2_confidence": title_vs_specs_a2_result.get("Confidence", "") or title_vs_specs_a2_result.get("confidence", ""),
                    "title_vs_specs_a2_reason": title_vs_specs_a2_result.get("Reason", "") or title_vs_specs_a2_result.get("reasoning", ""),
                    "title_vs_specs_a2_error": title_vs_specs_a2_result.get("error", ""),
                    "recheck_ran": str(batch_recheck_result.get("ran", False)).lower(),
                    "title_vs_cat_recheck_before": _batch_recheck_before.get("title_category_outlier", ""),
                    "title_vs_cat_recheck_verdict": (_batch_rc_by_agent.get("Title vs MCAT Agent") or {}).get("status", ""),
                    "title_vs_cat_recheck_reason": (_batch_rc_by_agent.get("Title vs MCAT Agent") or {}).get("reason", ""),
                    "title_vs_specs_recheck_before": _batch_recheck_before.get("title_spec_verdict", ""),
                    "title_vs_specs_recheck_verdict": (_batch_rc_by_agent.get("Title ISQ Agent") or {}).get("status", ""),
                    "title_vs_specs_recheck_reason": (_batch_rc_by_agent.get("Title ISQ Agent") or {}).get("reason", ""),
                    "specs_vs_cat_recheck_before": _batch_recheck_before.get("specs_category_outlier", ""),
                    "specs_vs_cat_recheck_verdict": (_batch_rc_by_agent.get("ISQ vs MCAT Agent") or {}).get("status", ""),
                    "specs_vs_cat_recheck_reason": (_batch_rc_by_agent.get("ISQ vs MCAT Agent") or {}).get("reason", ""),
                    "isq_recheck_before": _batch_recheck_before.get("isq", ""),
                    "isq_recheck_verdict": (_batch_rc_by_agent.get("Inter ISQ Agent") or {}).get("status", ""),
                    "isq_recheck_reason": (_batch_rc_by_agent.get("Inter ISQ Agent") or {}).get("reason", ""),
                    "bl_score": batch_scoring.get("composite_score", "") if batch_scoring.get("composite_score") is not None else "",
                    "bl_verdict": batch_scoring.get("verdict", ""),
                    "bl_score_2": batch_scoring_2.get("composite_score", "") if batch_scoring_2.get("composite_score") is not None else "",
                    "bl_verdict_2": batch_scoring_2.get("verdict", ""),
                    **_scoring_breakdown_fields(batch_scoring, "s1"),
                    **_scoring_breakdown_fields(batch_scoring_2, "s2"),
                    "completeness_score": batch_completeness.get("total_score", ""),
                    "completeness_pct": batch_completeness.get("percentage", ""),
                    "cs_title": (batch_completeness.get("breakdown") or {}).get("title", {}).get("score", ""),
                    "cs_quantity": (batch_completeness.get("breakdown") or {}).get("quantity", {}).get("score", ""),
                    "cs_spec_count": (batch_completeness.get("breakdown") or {}).get("spec_count", {}).get("score", ""),
                    "cs_predicted": (batch_completeness.get("breakdown") or {}).get("predicted_specs", {}).get("score", ""),
                    "cs_desc": (batch_completeness.get("breakdown") or {}).get("description", {}).get("score", ""),
                    "bl_quality_1": _compute_bl_quality_1(result, isq_result, desc_result, payload),
                    "bl_quality_2": _compute_bl_quality_2(title_vs_cat_a2_result or {}, specs_vs_cat_a2_result or {}, isq_result, desc_result, payload),
                    "bl_lq_completeness": _compute_bl_lq_completeness(batch_completeness),
                    "bl_high_value": "",
                    "quantity_audit_status": batch_quantity_audit.get("status", ""),
                    "quantity_audit_reason": batch_quantity_audit.get("reason", ""),
                    "buyer_viewed_vs_csl_status": batch_buyer_viewed_vs_csl_result.get("status", ""),
                    "buyer_viewed_vs_csl_reason": batch_buyer_viewed_vs_csl_result.get("reason", ""),
                    "buyer_viewed_vs_csl_error": batch_buyer_viewed_vs_csl_result.get("error", ""),
                })
            except Exception as exc:
                errors += 1
                try:
                    err_trace_id = trace.save(
                        item_name=(payload or {}).get("item_name", ""),
                        mcat_name=(payload or {}).get("mcat_name", ""),
                        audit_done_by=actor,
                    )
                except Exception:
                    err_trace_id = None
                try:
                    yield _sse({
                        "index": i,
                        "total": total,
                        "offer_id": offer_id,
                        "error": str(exc),
                        "failed_step": failed_step or "Unknown",
                        "steps": trace.steps,
                        "trace_id": err_trace_id,
                    })
                except Exception:
                    yield _sse({
                        "index": i,
                        "total": total,
                        "offer_id": offer_id,
                        "error": str(exc)[:500],
                        "failed_step": failed_step or "Unknown",
                        "steps": [],
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
    if "BuyLead returned no DATA" in msg or "offer may not exist" in msg:
        return f"Offer not found or unavailable. {msg[:200]}"
    if "codec can't encode" in msg or "charmap" in msg:
        return "A character encoding error occurred while saving the trace. The audit result was processed — please retry."
    if "timed out" in msg.lower() or "Timeout" in msg:
        return f"Upstream API timed out. Detail: {msg[:300]}"
    if "Connection" in msg or "ConnectError" in msg:
        return "Could not reach an upstream API. Check network connectivity and try again."
    if "HTTP " in msg or "HTTPStatusError" in msg or "status_code" in msg:
        return f"Upstream API returned an error response. Detail: {msg[:300]}"
    if "non-JSON" in msg:
        return f"Upstream API returned a non-JSON body. Detail: {msg[:300]}"
    if "Missing" in msg and "LLM" in msg:
        return msg
    return f"Audit pipeline error: {msg[:300]}"


@router.get("/admin_view", response_class=HTMLResponse)
async def admin_index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "sample_offer_id": "142764424452",
            "default_payload": json.dumps(DEFAULT_AUDIT_PAYLOAD, indent=2),
            "audit_url": "/admin_view/audit",
            "batch_url": "/admin_view/batch",
            "records_url": "/admin_view/records",
            "traces_url": "/admin_view/traces",
        },
    )


@router.get("/admin_view/records", response_class=HTMLResponse)
async def admin_records(request: Request):
    return templates.TemplateResponse(
        request,
        "records.html",
        {
            "rows": read_audit_dashboard_rows(),
            "show_retail_1": True,
            "home_url": "/admin_view",
            "trace_detail_base": "/admin_view/traces",
        },
    )


@router.get("/admin_view/batch", response_class=HTMLResponse)
async def admin_batch_page(request: Request):
    return templates.TemplateResponse(request, "batch.html", {
        "show_retail_1": True,
        "home_url": "/admin_view",
        "records_url": "/admin_view/records",
        "trace_detail_base": "/admin_view/traces",
    })


@router.get("/admin_view/traces", response_class=HTMLResponse)
async def admin_traces_list(request: Request):
    return templates.TemplateResponse(request, "traces.html", {
        "traces": list_traces(),
    })


@router.get("/admin_view/traces/{trace_id}", response_class=HTMLResponse)
async def admin_trace_detail(request: Request, trace_id: str):
    trace = get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return templates.TemplateResponse(request, "trace_detail.html", {
        "trace": trace,
        "show_retail_1": True,
    })


@router.post("/api/audit")
async def api_audit(payload: AuditPayload):
    """Raw JSON API endpoint - returns the upstream response directly."""
    try:
        result = await call_auditor_api(payload.model_dump())
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


def _build_agents_view(keys, user_email: str | None = None):
    out = []
    for key in keys:
        meta = PROMPT_AGENTS[key]
        content, is_override = get_active_prompt(key, user_email=user_email)
        out.append({
            "key": key,
            "display_name": meta["display_name"],
            "placeholders": meta["placeholders"],
            "content": content,
            "is_override": is_override,
        })
    return out


@router.get("/prompts", response_class=HTMLResponse)
async def prompts_page(request: Request):
    user_email = (request.session.get("user") or {}).get("email")
    return templates.TemplateResponse(request, "prompts.html", {
        "agents": _build_agents_view(PROMPT_PUBLIC, user_email=user_email),
        "is_admin": False,
        "save_url": "/prompts/save",
        "reset_url": "/prompts/reset",
    })


@router.get("/admin_view/prompts", response_class=HTMLResponse)
async def admin_prompts_page(request: Request):
    user_email = (request.session.get("user") or {}).get("email")
    return templates.TemplateResponse(request, "prompts.html", {
        "agents": _build_agents_view(list(PROMPT_AGENTS.keys()), user_email=user_email),
        "is_admin": True,
        "save_url": "/admin_view/prompts/save",
        "reset_url": "/admin_view/prompts/reset",
    })


async def _prompts_save(request: Request, allow_admin: bool):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    agent_key = str(body.get("agent", "")).strip()
    content = body.get("content", "")
    if agent_key not in PROMPT_AGENTS:
        raise HTTPException(status_code=400, detail="Unknown agent")
    if not allow_admin and agent_key in PROMPT_ADMIN_ONLY:
        raise HTTPException(status_code=403, detail="This prompt is editable only from the admin view")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content must be a string")
    user_email = (request.session.get("user") or {}).get("email")
    save_override(agent_key, content, user_email=user_email)
    _, is_override = get_active_prompt(agent_key, user_email=user_email)
    return {"ok": True, "is_override": is_override}


async def _prompts_reset(request: Request, allow_admin: bool):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    agent_key = str(body.get("agent", "")).strip()
    if agent_key not in PROMPT_AGENTS:
        raise HTTPException(status_code=400, detail="Unknown agent")
    if not allow_admin and agent_key in PROMPT_ADMIN_ONLY:
        raise HTTPException(status_code=403, detail="This prompt is editable only from the admin view")
    user_email = (request.session.get("user") or {}).get("email")
    reset_override(agent_key, user_email=user_email)
    content = get_bundled_prompt(agent_key)
    return {"ok": True, "content": content, "is_override": False}


@router.post("/prompts/save")
async def prompts_save(request: Request):
    return await _prompts_save(request, allow_admin=False)


@router.post("/prompts/reset")
async def prompts_reset(request: Request):
    return await _prompts_reset(request, allow_admin=False)


@router.post("/admin_view/prompts/save")
async def admin_prompts_save(request: Request):
    return await _prompts_save(request, allow_admin=True)


@router.post("/admin_view/prompts/reset")
async def admin_prompts_reset(request: Request):
    return await _prompts_reset(request, allow_admin=True)


@router.post("/clear-logs")
async def clear_logs():
    clear_audit_dashboard_log()
    clear_traces()
    return {"ok": True}


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
    buyer_result = {
        "status": "not_outlier",
        "reason": "Demo page does not call the buyer profile agent.",
        "product_matched": "0/0",
    }
    retail_agent_2_result = {
        "Display_id": "142764424452",
        "Classification": "UNCLASSIFIED",
        "Confidence": "None",
        "Reason": "Demo page does not call Retail Agent 2.",
    }

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "data": result,
            "payload": payload,
            "offer_id": "142764424452",
            "buylead_response": buylead_response,
            "buylead_raw_json": json.dumps(buylead_response, indent=2),
            "csv_path": "",
            "existing_retail_flag": extract_existing_retail_flag(buylead_response),
            "raw_json": json.dumps(result, indent=2),
            "buyer_result": buyer_result,
            "buyer_raw_json": json.dumps(buyer_result, indent=2),
            "retail_agent_2_result": retail_agent_2_result,
            "retail_agent_2_raw_json": json.dumps(retail_agent_2_result, indent=2),
        },
    )


@router.get("/download/audit-dashboard-log")
async def download_audit_dashboard_log():
    path = _DATA_DIR / "audit_dashboard_log.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail="audit_dashboard_log.csv not found")
    _ensure_csv_headers()
    return FileResponse(str(path), media_type="text/csv", filename="audit_dashboard_log.csv")


@router.get("/download/audit-traces")
async def download_audit_traces():
    path = _DATA_DIR / "audit_traces.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail="audit_traces.csv not found")
    return FileResponse(str(path), media_type="text/csv", filename="audit_traces.csv")
