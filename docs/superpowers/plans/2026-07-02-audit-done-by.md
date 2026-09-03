# audit_done_by Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the email of the user who performed each audit as a new `audit_done_by` column in both the trace log and the dashboard log; consumer/worker audits record `"admin"`; shown on `/records` and `/traces` (list + detail) but not on `result.html`.

**Architecture:** A single helper `_audit_actor(request)` derives the email from the signed session (`"admin"` when there's no session user, i.e. the trusted-peer consumer). It's threaded into the two write paths (`_audit_handler`, `batch_stream`). Both CSV schemas gain a trailing `audit_done_by` column with a one-time header migration so existing files stay aligned; missing/empty values render as `"unknown"`.

**Tech Stack:** FastAPI/Starlette, Python `csv`, Jinja2, pytest.

## Global Constraints

- CSV schemas are order-sensitive — `audit_done_by` is appended as the LAST column of each schema; never reorder existing columns.
- Actor rule: logged-in user → their session email; no session user (consumer via trusted-peer `/audit`) → literal `"admin"`.
- Old rows (written before this feature) display `"unknown"`, never blank.
- `result.html` (live post-audit dashboard) must NOT show the column.
- `records.html` builds columns dynamically from `rows[0].keys()`, so it needs NO template edit — the column appears automatically once the schema has it.
- Follow existing patterns: `audit_log_service._ensure_csv_headers()` already migrates the dashboard CSV by comparing headers to `CSV_HEADERS` and rewriting normalized rows. `trace_service` has no such migration and needs one added.
- `AuditTrace.save(...)` and `append_audit_dashboard_row(...)` get `audit_done_by="admin"` as the default so any un-threaded caller is safe.
- pytest is already a dependency; tests live in `tests/`.

## File Structure

- Modify `app/services/trace_service.py` — schema, header migration, save param, row read.
- Modify `app/services/audit_log_service.py` — schema, append param, read normalization.
- Modify `app/routers/audit.py` — `_audit_actor` helper; thread into single + batch write sites; add `request` to `batch_stream`.
- Modify `app/templates/traces.html` — add "Done By" column.
- Modify `app/templates/trace_detail.html` — add "Done By" to header meta.
- Test `tests/test_trace_service_actor.py`, `tests/test_audit_log_actor.py`, `tests/test_audit_actor.py`.

---

### Task 1: trace_service — `audit_done_by` column + migration

**Files:**
- Modify: `app/services/trace_service.py`
- Test: `tests/test_trace_service_actor.py`

**Interfaces:**
- Produces:
  - `CSV_FIELDS` ends with `"audit_done_by"`.
  - `AuditTrace.save(self, item_name="", mcat_name="", audit_done_by="admin") -> str`
  - `_data_to_row(data)` writes `audit_done_by` from `data.get("audit_done_by") or ""`.
  - `_row_to_summary(row)` and `_row_to_full(row)` include key `"audit_done_by"` = `row.get("audit_done_by") or "unknown"`.
  - `_migrate_header()` — if `TRACES_CSV` exists and its header != `CSV_FIELDS`, rewrite the file with the new header and rows normalized to `CSV_FIELDS`. Called inside `_ensure_csv()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trace_service_actor.py`:

```python
import csv
import importlib

import app.services.trace_service as ts


def _reload_with_dir(tmp_path, monkeypatch):
    # Point the module's TRACES_CSV at a temp file for isolation.
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    importlib.reload(ts)
    return ts


def test_save_persists_audit_done_by(tmp_path, monkeypatch):
    mod = _reload_with_dir(tmp_path, monkeypatch)
    t = mod.AuditTrace("123")
    t.add_step("s", "transform")
    trace_id = t.save(item_name="Widget", mcat_name="MC", audit_done_by="a@indiamart.com")
    full = mod.get_trace(trace_id)
    assert full["audit_done_by"] == "a@indiamart.com"
    summaries = {s["trace_id"]: s for s in mod.list_traces()}
    assert summaries[trace_id]["audit_done_by"] == "a@indiamart.com"


def test_save_defaults_to_admin(tmp_path, monkeypatch):
    mod = _reload_with_dir(tmp_path, monkeypatch)
    t = mod.AuditTrace("124")
    t.add_step("s", "transform")
    trace_id = t.save(item_name="X", mcat_name="Y")
    assert mod.get_trace(trace_id)["audit_done_by"] == "admin"


def test_missing_column_reads_as_unknown(tmp_path, monkeypatch):
    mod = _reload_with_dir(tmp_path, monkeypatch)
    # Write an OLD-format trace file: header without audit_done_by.
    old_fields = [f for f in mod.CSV_FIELDS if f != "audit_done_by"]
    with mod.TRACES_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=old_fields)
        w.writeheader()
        w.writerow({
            "trace_id": "900_x", "offer_id": "900", "item_name": "Old",
            "mcat_name": "M", "started_at": "2026-06-01T00:00:00",
            "completed_at": "2026-06-01T00:00:01", "total_steps": "1",
            "has_error": "false", "steps_json": "[]",
        })
    # Reading triggers _migrate_header via _ensure_csv; old row → "unknown".
    full = mod.get_trace("900_x")
    assert full["audit_done_by"] == "unknown"
    # After migration a NEW row round-trips correctly.
    t = mod.AuditTrace("901")
    t.add_step("s", "transform")
    tid = t.save(audit_done_by="b@intermesh.net")
    assert mod.get_trace(tid)["audit_done_by"] == "b@intermesh.net"
    # And the old row is still readable as unknown after the new append.
    assert mod.get_trace("900_x")["audit_done_by"] == "unknown"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_trace_service_actor.py -v`
Expected: FAIL (e.g. `KeyError: 'audit_done_by'` / `AssertionError`), because the column doesn't exist yet.

- [ ] **Step 3: Add the column to `CSV_FIELDS`**

In `app/services/trace_service.py`, append `"audit_done_by"` as the last entry:

```python
CSV_FIELDS = [
    "trace_id",
    "offer_id",
    "item_name",
    "mcat_name",
    "started_at",
    "completed_at",
    "total_steps",
    "has_error",
    "steps_json",
    "audit_done_by",
]
```

- [ ] **Step 4: Add `_migrate_header()` and call it from `_ensure_csv()`**

Add this function (place it just after `_repair_csv`):

```python
def _migrate_header() -> None:
    """If an existing traces file predates a schema column, rewrite it with the
    current CSV_FIELDS header so DictReader/DictWriter stay column-aligned.
    Old rows gain empty values for new columns."""
    if not TRACES_CSV.exists():
        return
    with TRACES_CSV.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing = reader.fieldnames or []
        if existing == CSV_FIELDS:
            return
        rows = list(reader)
    with TRACES_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in CSV_FIELDS})
```

Then call it in `_ensure_csv()` after the repair, so reads/appends see the new header. Change:

```python
def _ensure_csv() -> None:
    if TRACES_CSV.exists():
        _repair_csv()
        _migrate_header()
        return
    TRACES_CSV.parent.mkdir(parents=True, exist_ok=True)
    with TRACES_CSV.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()
    _migrate_legacy_json()
```

- [ ] **Step 5: Thread `audit_done_by` through save + row builders**

In `_data_to_row`, add the field (uses `""` default so legacy JSON migration → later shown as "unknown"):

```python
def _data_to_row(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "trace_id": data.get("trace_id", ""),
        "offer_id": data.get("offer_id", ""),
        "item_name": data.get("item_name", "") or "",
        "mcat_name": data.get("mcat_name", "") or "",
        "started_at": data.get("started_at", ""),
        "completed_at": data.get("completed_at", ""),
        "total_steps": data.get("total_steps", 0),
        "has_error": "true" if data.get("has_error") else "false",
        "steps_json": json.dumps(data.get("steps", []), ensure_ascii=False, default=str),
        "audit_done_by": data.get("audit_done_by") or "",
    }
```

In `_row_to_summary`, add to the returned dict:

```python
        "has_error": (row.get("has_error") or "").lower() == "true",
        "duration_ms": duration_ms,
        "audit_done_by": row.get("audit_done_by") or "unknown",
    }
```

In `_row_to_full`, add to the returned dict (anywhere in the dict literal):

```python
        "started_at_display": summary["started_at"],
        "audit_done_by": summary["audit_done_by"],
    }
```

Update `AuditTrace.save` signature and the `_data_to_row` call:

```python
    def save(self, item_name: str = "", mcat_name: str = "", audit_done_by: str = "admin") -> str:
        ts = self.started_at.strftime("%Y%m%d_%H%M%S")
        trace_id = f"{self.offer_id}_{ts}"
        row = _data_to_row({
            "trace_id": trace_id,
            "offer_id": self.offer_id,
            "item_name": item_name,
            "mcat_name": mcat_name,
            "started_at": self.started_at.isoformat(),
            "completed_at": datetime.now().isoformat(),
            "total_steps": self._step,
            "has_error": any(s["status"] == "error" for s in self._steps),
            "steps": self._steps,
            "audit_done_by": audit_done_by,
        })
        _append_row(row)
        return trace_id
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_trace_service_actor.py -v`
Expected: PASS (3/3).

- [ ] **Step 7: Commit**

```bash
git add app/services/trace_service.py tests/test_trace_service_actor.py
git commit -m "feat(traces): add audit_done_by column + header migration"
```

---

### Task 2: audit_log_service — `audit_done_by` column

**Files:**
- Modify: `app/services/audit_log_service.py`
- Test: `tests/test_audit_log_actor.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `CSV_HEADERS` ends with `"audit_done_by"`.
  - `append_audit_dashboard_row(..., audit_done_by: str = "admin")` writes it into the row.
  - `read_audit_dashboard_rows()` returns each row with `audit_done_by` normalized to `"unknown"` when missing/empty.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audit_log_actor.py`:

```python
import csv
import importlib

import app.services.audit_log_service as als


def _reload_with_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    importlib.reload(als)
    return als


def test_append_persists_actor(tmp_path, monkeypatch):
    mod = _reload_with_dir(tmp_path, monkeypatch)
    mod.append_audit_dashboard_row(
        "111", {"item_name": "W", "mcat_name": "M", "price": "1"}, {},
        audit_done_by="a@indiamart.com",
    )
    rows = mod.read_audit_dashboard_rows()
    assert rows[0]["audit_done_by"] == "a@indiamart.com"


def test_append_defaults_to_admin(tmp_path, monkeypatch):
    mod = _reload_with_dir(tmp_path, monkeypatch)
    mod.append_audit_dashboard_row("112", {"item_name": "W"}, {})
    rows = mod.read_audit_dashboard_rows()
    assert rows[0]["audit_done_by"] == "admin"


def test_old_row_reads_as_unknown(tmp_path, monkeypatch):
    mod = _reload_with_dir(tmp_path, monkeypatch)
    # Write an OLD-format dashboard file: header without audit_done_by.
    old_headers = [h for h in mod.CSV_HEADERS if h != "audit_done_by"]
    with open(mod.CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=old_headers)
        w.writeheader()
        w.writerow({h: "" for h in old_headers} | {"offer_id": "222"})
    rows = mod.read_audit_dashboard_rows()
    assert rows[0]["offer_id"] == "222"
    assert rows[0]["audit_done_by"] == "unknown"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_audit_log_actor.py -v`
Expected: FAIL (`KeyError: 'audit_done_by'`), column not present yet.

- [ ] **Step 3: Add the column to `CSV_HEADERS`**

In `app/services/audit_log_service.py`, append `"audit_done_by"` as the LAST entry of the `CSV_HEADERS` list (immediately after `"buyer_profile_error"`):

```python
    "buyer_profile_error",
    # Email of the user who ran the audit; "admin" for consumer/worker runs.
    "audit_done_by",
]
```

- [ ] **Step 4: Add the parameter and row entry to `append_audit_dashboard_row`**

Add the parameter to the signature (after `buyer_profile_response`):

```python
    buyer_profile_response: Optional[Dict[str, Any]] = None,
    audit_done_by: str = "admin",
) -> str:
```

Add the row entry (immediately after the `"buyer_profile_error"` entry in the `row` dict):

```python
        "buyer_profile_error": buyer_profile_response.get("error", ""),
        "audit_done_by": audit_done_by or "admin",
    }
```

- [ ] **Step 5: Normalize missing/empty on read**

In `read_audit_dashboard_rows()`, change the row-building loop so a missing/empty value becomes `"unknown"`:

```python
        rows = []
        for raw in reader:
            row = {k: v for k, v in raw.items() if isinstance(k, str)}
            row["audit_done_by"] = row.get("audit_done_by") or "unknown"
            rows.append(row)
    rows.reverse()
    return rows
```

(`_ensure_csv_headers()` already migrates an old file to include the new column — old data rows get `""`, then normalized to `"unknown"` here.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_audit_log_actor.py -v`
Expected: PASS (3/3).

- [ ] **Step 7: Commit**

```bash
git add app/services/audit_log_service.py tests/test_audit_log_actor.py
git commit -m "feat(records): add audit_done_by column to dashboard log"
```

---

### Task 3: Router — `_audit_actor` helper + thread into write paths

**Files:**
- Modify: `app/routers/audit.py`
- Test: `tests/test_audit_actor.py`

**Interfaces:**
- Consumes: `AuditTrace.save(..., audit_done_by=...)` (Task 1), `append_audit_dashboard_row(..., audit_done_by=...)` (Task 2).
- Produces: `_audit_actor(request) -> str` in `app/routers/audit.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_audit_actor.py`:

```python
from types import SimpleNamespace

from app.routers.audit import _audit_actor


def _req(session):
    return SimpleNamespace(session=session)


def test_logged_in_user_returns_email():
    assert _audit_actor(_req({"user": {"email": "a@indiamart.com"}})) == "a@indiamart.com"


def test_no_user_returns_admin():
    assert _audit_actor(_req({})) == "admin"


def test_user_without_email_returns_admin():
    assert _audit_actor(_req({"user": {}})) == "admin"


def test_none_request_returns_admin():
    assert _audit_actor(None) == "admin"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_audit_actor.py -v`
Expected: FAIL (`ImportError: cannot import name '_audit_actor'`).

- [ ] **Step 3: Add the `_audit_actor` helper**

In `app/routers/audit.py`, add this function just above `_audit_handler` (near line 226):

```python
def _audit_actor(request) -> str:
    """Email of the user who triggered the audit; 'admin' for the consumer
    (trusted-peer /audit with no session user)."""
    user = request.session.get("user") if request is not None else None
    return (user or {}).get("email") or "admin"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_audit_actor.py -v`
Expected: PASS (4/4).

- [ ] **Step 5: Thread the actor into the single-audit write path**

In `_audit_handler`, compute the actor once right after the `trace = AuditTrace(offer_id)` line (near line 239):

```python
    trace = AuditTrace(offer_id)
    actor = _audit_actor(request)
```

Pass it to the success-path `trace.save` (near line 637):

```python
            trace_id = trace.save(
                item_name=result.get("item_name") or payload.get("item_name", ""),
                mcat_name=payload.get("mcat_name", ""),
                audit_done_by=actor,
            )
```

Pass it to `append_audit_dashboard_row` (add as the last argument, near line 659):

```python
            activity_keywords=", ".join(activity_keywords),
            quantity_audit_response=quantity_audit_result,
            buyer_profile_response=buyer_profile_result,
            audit_done_by=actor,
        )
```

Pass it to the error-path `trace.save` (near line 663):

```python
            trace_id = trace.save(
                item_name=(payload or {}).get("item_name", ""),
                mcat_name=(payload or {}).get("mcat_name", ""),
                audit_done_by=actor,
            )
```

- [ ] **Step 6: Thread the actor into the batch write path**

Add `request` to the `batch_stream` signature (near line 1018) so FastAPI injects it (`Request` is already imported in this file):

```python
@router.get("/batch/stream")
async def batch_stream(request: Request, offer_ids: str = ""):
    ids = [oid.strip() for oid in offer_ids.split(",") if oid.strip()]
    total = len(ids)
    actor = _audit_actor(request)
```

Pass `actor` to the OK-path `trace.save` (near line 1353):

```python
                    ok_trace_id = trace.save(
                        item_name=result.get("item_name") or payload.get("item_name", ""),
                        mcat_name=payload.get("mcat_name", ""),
                        audit_done_by=actor,
                    )
```

Pass `actor` as the last argument to the batch `append_audit_dashboard_row` (near line 1403 — after its final existing argument, mirroring the single-audit call):

```python
                    quantity_audit_response=batch_quantity_audit,
                    buyer_profile_response=buyer_profile_result,
                    audit_done_by=actor,
                )
```

(If the batch `append_audit_dashboard_row(...)` call's final existing arguments differ, keep them and simply add `audit_done_by=actor,` as the last keyword argument. Read the call at lines 1403–1420 first and append the kwarg.)

Pass `actor` to the error-path `trace.save` in the batch loop (near line 1505):

```python
                    err_trace_id = trace.save(
                        item_name=(payload or {}).get("item_name", ""),
                        mcat_name=(payload or {}).get("mcat_name", ""),
                        audit_done_by=actor,
                    )
```

- [ ] **Step 7: Verify the app still imports and the actor test passes**

Run: `python -m pytest tests/test_audit_actor.py -v`
Then: `set GOOGLE_CLIENT_ID=x && set GOOGLE_CLIENT_SECRET=y && set SESSION_SECRET=z && set OAUTH_REDIRECT_BASE_URL=http://localhost:8000 && python -c "import main; print('import ok')"`
(bash: `GOOGLE_CLIENT_ID=x GOOGLE_CLIENT_SECRET=y SESSION_SECRET=z OAUTH_REDIRECT_BASE_URL=http://localhost:8000 python -c "import main; print('import ok')"`)
Expected: tests PASS; prints `import ok` (confirms no syntax error and `batch_stream` signature is valid).

- [ ] **Step 8: Commit**

```bash
git add app/routers/audit.py tests/test_audit_actor.py
git commit -m "feat(audit): record audit_done_by actor for single + batch audits"
```

---

### Task 4: Templates — show `audit_done_by` on traces list + trace detail

**Files:**
- Modify: `app/templates/traces.html`
- Modify: `app/templates/trace_detail.html`

**Interfaces:**
- Consumes: `t.audit_done_by` (from `_row_to_summary`, Task 1) and `trace.audit_done_by` (from `_row_to_full`, Task 1).
- Produces: nothing consumed by later tasks.

(No `records.html` change: it renders columns dynamically from `rows[0].keys()`, so the new schema column appears automatically.)

- [ ] **Step 1: Add the column to the traces list table**

In `app/templates/traces.html`, add a header cell after the `MCAT` `<th>` (line 31):

```html
          <th>MCAT</th>
          <th>Done By</th>
          <th>Started At</th>
```

And add the matching body cell after the MCAT `<td>` (line 47):

```html
          <td style="color:var(--text-secondary);">{{ t.mcat_name or '—' }}</td>
          <td style="color:var(--text-secondary);font-size:0.75rem;">{{ t.audit_done_by }}</td>
          <td style="color:var(--text-muted);font-size:0.75rem;white-space:nowrap;">{{ t.started_at }}</td>
```

- [ ] **Step 2: Add "Done By" to the trace-detail header meta**

In `app/templates/trace_detail.html`, add a span in the meta row (after the MCAT span, line 15):

```html
        {% if trace.mcat_name %}<span>MCAT: <b style="color:var(--text-secondary);">{{ trace.mcat_name }}</b></span>{% endif %}
        <span>Done By: <b style="color:var(--text-secondary);">{{ trace.audit_done_by }}</b></span>
        <span>Started: <b style="color:var(--text-secondary);">{{ trace.started_at_display }}</b></span>
```

- [ ] **Step 3: Verify templates render (manual, quick)**

Start the app (`uvicorn main:app --reload --port 8000` with auth env set), log in, run one audit, then open `/traces` and click "Steps" on the row. Confirm the list shows a "Done By" column with your email and the detail header shows "Done By: <your email>".

- [ ] **Step 4: Commit**

```bash
git add app/templates/traces.html app/templates/trace_detail.html
git commit -m "feat(ui): show audit_done_by on traces list + trace detail"
```

---

### Task 5: End-to-end verification

**Files:** none (verification only).

**Interfaces:** none.

- [ ] **Step 1: Full suite passes**

Run: `python -m pytest tests/ -v`
Expected: all tests pass (Task 1–3 tests + pre-existing auth tests), output pristine.

- [ ] **Step 2: Human single + batch audit shows email**

With the app running and logged in as an `@indiamart.com` user, run a single audit and a batch audit, then:
- `/records` → the last column "Audit Done By" shows your email for the new rows.
- `/traces` → "Done By" column shows your email; open a trace's detail → header shows it.
- Confirm the live `result.html` page after the audit does NOT show the column.

- [ ] **Step 3: Consumer path records "admin"**

From the app host, unauthenticated (simulates the consumer's trusted-peer call):

```bash
curl -s -o /dev/null -X POST http://localhost:8000/audit -H "Content-Type: application/json" -d "{\"offer_id\":\"<a-valid-offer-id>\"}"
```

Then reload `/records` and `/traces`: the new row/trace shows `admin` in the Done By column.

- [ ] **Step 4: Old rows show "unknown"**

Confirm any records/traces created before this change display `unknown` (not blank) in the Done By column.

---

## Self-Review

**Spec coverage:**
- Actor resolution (`_audit_actor`, email vs `admin`) → Task 3. ✓
- Store in `audit_traces.csv` → Task 1; store in `audit_dashboard_log.csv` → Task 2. ✓
- Append as last column + header migration (both files) → Task 1 (`_migrate_header`), Task 2 (relies on existing `_ensure_csv_headers`). ✓
- Threading into single + batch (incl. `request` on `batch_stream`) → Task 3. ✓
- Show on `/records` (auto), `/traces` list + detail → Task 4; NOT on `result.html` → untouched (verified Task 5 Step 2). ✓
- Old rows → `"unknown"` → Task 1 (`_row_to_*`), Task 2 (`read_audit_dashboard_rows`). ✓
- Consumer → `"admin"` → Task 3 (`_audit_actor` default) + verified Task 5 Step 3. ✓
- Tests for actor + round-trip + unknown fallback → Tasks 1–3. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. The batch `append_audit_dashboard_row` note (Task 3 Step 6) instructs reading the exact call lines before appending the kwarg — this is a real safeguard, not a placeholder (the kwarg to add is fully specified).

**Type consistency:** `audit_done_by` is a `str` everywhere; `save(..., audit_done_by="admin")` and `append_audit_dashboard_row(..., audit_done_by="admin")` signatures match their call sites in Task 3; `_row_to_summary`/`_row_to_full` expose `audit_done_by` consumed verbatim by the Task 4 templates (`t.audit_done_by`, `trace.audit_done_by`).
