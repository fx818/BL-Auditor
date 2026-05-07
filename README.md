# BL Auditor

BL Auditor is a FastAPI web application for auditing IndiaMART BuyLead offers. Given a BuyLead offer ID, the app fetches BuyLead details, builds the existing product audit payload, calls the product outlier auditor API, runs a LangGraph retail classification agent, renders the combined result, and persists a dashboard-friendly CSV log.

## What It Does

- Accepts a numeric BuyLead offer ID from the web UI.
- Fetches live BuyLead details from the BuyLead detail API.
- Maps BuyLead fields into the existing auditor API payload.
- Calls the external product categorization/outlier auditor API.
- Runs a retail classification agent built in LangGraph.
- Uses local Excel data for retail price and evidence signals.
- Displays audit checks, price flag, retail classification, raw payloads, and raw responses.
- Logs each run to `audit_dashboard_log.csv`.
- Provides a `/records` page to review historical audit rows.

## Architecture

```text
Browser UI
   |
   | POST /audit { offer_id }
   v
FastAPI router: app/routers/audit.py
   |
   |-- fetch_buylead_detail()
   |      app/services/buylead_service.py
   |      -> BuyLead Detail API
   |
   |-- build_audit_payload_from_buylead()
   |      maps BL response to auditor payload
   |
   |-- call_auditor_api()
   |      app/services/auditor_service.py
   |      -> External product outlier auditor API
   |
   |-- run_retail_agent()
   |      app/retail_agent/agent.py
   |      -> LangGraph + OpenAI-compatible chat model
   |      -> mcat_data.xlsx + evidence_data.xlsx
   |
   |-- append_audit_dashboard_row()
   |      app/services/audit_log_service.py
   |      -> audit_dashboard_log.csv
   |
   v
Jinja result page: app/templates/result.html
```

## File Structure

```text
BL-Auditor/
|-- main.py
|-- requirements.txt
|-- .env.example
|-- README.md
|-- audit_dashboard_log.csv
|-- retail_agent.json
|-- BL_Detail_response.json
|-- audit_resp.json
|-- mcat_data.xlsx
|-- evidence_data.xlsx
|-- payload.txt
|-- app/
|   |-- __init__.py
|   |-- models/
|   |   |-- __init__.py
|   |   `-- schemas.py
|   |-- routers/
|   |   |-- __init__.py
|   |   `-- audit.py
|   |-- services/
|   |   |-- __init__.py
|   |   |-- buylead_service.py
|   |   |-- auditor_service.py
|   |   `-- audit_log_service.py
|   |-- retail_agent/
|   |   |-- __init__.py
|   |   |-- agent.py
|   |   `-- prompt.md
|   `-- templates/
|       |-- base.html
|       |-- index.html
|       |-- result.html
|       |-- records.html
|       |-- result_part1.html
|       |-- result_part2.html
|       |-- result_part3.html
|       `-- result_part4.html
`-- static/
    |-- css/
    |   `-- style.css
    `-- js/
        `-- app.js
```

File responsibilities:

- `main.py`: FastAPI application entrypoint.
- `app/routers/audit.py`: request routing and orchestration for the audit workflow.
- `app/services/buylead_service.py`: BuyLead detail API call and BL-to-audit-payload mapping.
- `app/services/auditor_service.py`: existing external auditor API client.
- `app/services/audit_log_service.py`: CSV header management, append logic, and records reader.
- `app/retail_agent/agent.py`: LangGraph retail-agent workflow and n8n JS-to-Python transformations.
- `app/retail_agent/prompt.md`: retail classification system prompt.
- `app/templates/index.html`: offer-ID form.
- `app/templates/result.html`: combined audit and retail result page.
- `app/templates/records.html`: historical CSV records page.
- `static/js/app.js`: browser-side form submission and UI helpers.
- `static/css/style.css`: UI styling.
- `mcat_data.xlsx`: MCAT price data used by the retail agent.
- `evidence_data.xlsx`: marketplace evidence data used by the retail agent.
- `audit_dashboard_log.csv`: persisted combined audit and retail output.
- `retail_agent.json`: original n8n workflow reference.

## Workflow Map For Future Agents

This is the practical code path to inspect when changing behavior:

```text
1. User submits offer ID
   static/js/app.js
   -> POST /audit

2. FastAPI receives request
   app/routers/audit.py
   -> audit()

3. BuyLead data is fetched
   app/services/buylead_service.py
   -> fetch_buylead_detail()

4. Existing auditor payload is built
   app/services/buylead_service.py
   -> build_audit_payload_from_buylead()

5. Existing product auditor API is called
   app/services/auditor_service.py
   -> call_auditor_api()

6. Retail agent is called with the same BL response
   app/retail_agent/agent.py
   -> run_retail_agent()
   -> _build_offer_source()
   -> _bl_detail_and_keys()
   -> _merge_inputs()
   -> LangGraph classify node

7. Retail agent loads local supporting data
   app/retail_agent/agent.py
   -> _load_evidence_metrics() reads evidence_data.xlsx
   -> _find_price_data() reads mcat_data.xlsx

8. Combined row is saved
   app/services/audit_log_service.py
   -> append_audit_dashboard_row()
   -> audit_dashboard_log.csv

9. Result page is rendered
   app/templates/result.html
```

When modifying the retail agent, compare changes against `retail_agent.json` first, then update `app/retail_agent/agent.py` and `app/retail_agent/prompt.md`.

## Main Components

### Web App

- `main.py` creates the FastAPI app and mounts static assets.
- `app/routers/audit.py` owns the web routes:
  - `GET /` renders the offer-ID input page.
  - `POST /audit` runs the full BL audit flow and renders the result page.
  - `GET /records` renders rows saved in the CSV log.
  - `POST /api/audit` exposes the raw existing auditor API wrapper for a supplied audit payload.
  - `GET /demo` renders the result template with local demo data when available.

### BuyLead Service

File: `app/services/buylead_service.py`

Responsibilities:

- Calls the BuyLead detail API.
- Extracts `RESPONSE.DATA` from the BuyLead response.
- Converts `ENRICHMENTINFO` into the existing auditor `ISQ` format.
- Builds the product audit payload from BuyLead fields.

Important mappings:

- `pc_item_id` from offer ID.
- `item_name` from `ETO_OFR_TITLE`.
- `item_desc` from `ETO_OFR_DESC`.
- `mcat_name` from `PRIME_MCAT_NAME`.
- `mcat_id` from `MCAT_IDS`.
- `price` from `ETO_OFR_APPROX_ORDER_VALUE`.

### Auditor Service

File: `app/services/auditor_service.py`

Responsibilities:

- Calls the existing external product categorization/outlier API.
- Sends the mapped audit payload as JSON.
- Raises a useful error if the upstream API returns a non-2xx response.

The auditor response powers the existing product/category checks, photo/title checks, title/spec checks, and price flag displayed on the result page.

### Retail Agent

Folder: `app/retail_agent/`

The retail agent is a LangGraph implementation of the provided n8n workflow in `retail_agent.json`.

Files:

- `agent.py` contains the Python workflow implementation.
- `prompt.md` contains the retail classification prompt.
- `__init__.py` exports `run_retail_agent`.

The agent reproduces the important n8n nodes:

- `BL Detail & Keys`: parses BL details, ISQ data, quantity, order value, buyer profile, BL card data, retail flag, and MCAT/unit key.
- `Data Aligator`: aggregates evidence rows by MCAT, normalized unit, and quantity slab.
- `Code in JavaScript1`: merges offer data, price data, slab metrics, and marketplace signals.
- `Retail Agent`: calls an OpenAI-compatible chat model through LangGraph.
- `Classifier O/P Cleaning`: strips markdown code fences and parses strict JSON.

Retail output format:

```json
{
  "Display_id": "142764424452",
  "Classification": "RETAIL | NON-RETAIL | UNCLASSIFIED",
  "Classi_Score": 0.8,
  "Confidence": "High | Medium | Low | None",
  "Override_Applied": "Yes - rule name | No",
  "Reason": "Short classification reason"
}
```

If the retail agent fails, the main audit request still succeeds. The app logs a fallback retail result with `Classification = UNCLASSIFIED` and stores the failure in `retail_error`.

### Data Files

- `mcat_data.xlsx`: local replacement for the n8n `MCAT_Price` Google Sheet.
- `evidence_data.xlsx`: local replacement for the n8n `Get row(s) in sheet` evidence sheet.
- `BL_Detail_response.json`: sample BuyLead detail API response for local inspection/testing.
- `retail_agent.json`: original n8n workflow reference.
- `audit_dashboard_log.csv`: persistent dashboard log.

### CSV Logging

File: `app/services/audit_log_service.py`

The CSV log stores one row per audit run. Existing audit fields are preserved and retail fields are appended.

Retail columns:

- `retail_classification`
- `retail_classi_score`
- `retail_confidence`
- `retail_override_applied`
- `retail_reason`
- `retail_raw_json`
- `retail_error`

The logger automatically expands older CSV files to the current header shape.

## Configuration

Create a `.env` file based on `.env.example`:

```env
RETAIL_LLM_BASE_URL=
RETAIL_LLM_API_KEY=
RETAIL_LLM_MODEL=
RETAIL_LLM_TIMEOUT=60
```

Notes:

- `RETAIL_LLM_API_KEY` is required for real retail agent calls.
- `RETAIL_LLM_MODEL` is required.
- `RETAIL_LLM_BASE_URL` is optional for default OpenAI usage, but should be set for OpenAI-compatible providers.
- `RETAIL_LLM_TIMEOUT` defaults to `60` seconds.

The current code reads these values from environment variables. If you use a `.env` file, load it through your shell, process manager, or add dotenv loading before app startup.

## Installation

```bash
python -m pip install -r requirements.txt
```

Main dependencies:

- `fastapi`: web application framework.
- `uvicorn`: ASGI server.
- `httpx`: async HTTP client for upstream APIs.
- `jinja2`: server-rendered templates.
- `pydantic`: request/response models.
- `langgraph`: retail agent workflow graph.
- `langchain-openai`: OpenAI-compatible chat model integration.
- `openpyxl`: Excel workbook reading for local retail data.

## Running Locally

```bash
uvicorn main:app --reload
```

Then open:

```text
http://127.0.0.1:8000
```

Useful routes:

- `/`: run a new audit by offer ID.
- `/records`: view saved CSV audit records.
- `/demo`: render a demo result page from local sample files when present.
- `/api/audit`: raw JSON auditor API wrapper.

## End-to-End Flow

1. User enters an offer ID in the browser.
2. The frontend posts `{ "offer_id": "..." }` to `/audit`.
3. The app validates the offer ID.
4. The BuyLead service fetches live BL details.
5. The BL response is mapped into the existing product auditor payload.
6. The external auditor API returns outlier and price verdicts.
7. The retail agent uses the same BL response plus local Excel data to classify retail vs non-retail.
8. The result page renders both product audit and retail classification outputs.
9. A combined row is saved to `audit_dashboard_log.csv`.

## Error Handling

- Invalid request body returns `400`.
- Missing or non-numeric offer ID returns `400`.
- BuyLead API or existing auditor API failure returns `502`.
- Retail agent failure does not fail the full audit request. It is captured as a fallback retail response and logged in `retail_error`.

## Development Notes

- Keep the retail prompt in `app/retail_agent/prompt.md` so prompt changes are easy to review.
- Keep n8n parity logic in `app/retail_agent/agent.py` when translating workflow changes.
- `mcat_data.xlsx` is large, so the retail agent performs targeted price lookup by MCAT/unit key instead of loading the entire workbook into memory.
- `evidence_data.xlsx` is aggregated and cached in process for repeated calls.
- Historical CSV rows are preserved when headers change.
