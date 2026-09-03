# Design — `audit_done_by` tracking

**Date:** 2026-07-02
**Status:** Approved (design), pending implementation plan

## Goal

Record which authenticated user performed each audit (single or batch) and
persist their email as a new `audit_done_by` column in both the dashboard log
and the trace log. Audits triggered by the RabbitMQ consumer/worker (no login)
record the literal `"admin"`. The column is shown on `/records` and `/traces`
(list + detail) but **not** on the live post-audit dashboard (`result.html`).

## Non-goals

- No per-user filtering, roles, or auth changes.
- No change to `result.html`.
- No backfill of real emails onto pre-existing rows (they show `"unknown"`).

## Actor resolution

Single helper in `app/routers/audit.py`:

```python
def _audit_actor(request) -> str:
    user = request.session.get("user") if request else None
    return (user or {}).get("email") or "admin"
```

- Any logged-in user reaching a gated audit route (`/audit`, `/admin_view/audit`,
  `/batch/stream`, `/api/audit`) → their session email.
- The consumer hits `/audit` via the trusted-peer IP exemption with no session
  `user` → `"admin"`.
- `SessionMiddleware` is outermost, so `request.session` is always present; the
  helper never raises.

## Storage — append one column to both CSVs

Both logs get `audit_done_by` as the **last** column (schema is order-sensitive,
so append only — never reorder existing columns).

**`audit_traces.csv`** (`app/services/trace_service.py`):
- Add `"audit_done_by"` as the final entry of `CSV_FIELDS`.
- `AuditTrace.save(item_name="", mcat_name="", audit_done_by="admin")` — new
  keyword param (default `"admin"` so any un-threaded caller is safe).
- `_data_to_row` writes it; `_row_to_summary` and `_row_to_full` read it,
  substituting `"unknown"` when the key is missing/empty (old rows).

**`audit_dashboard_log.csv`** (`app/services/audit_log_service.py`):
- Add `"audit_done_by"` as the final column in the CSV field schema and in the
  row-builder used by `append_audit_dashboard_row(...)`.
- `read_audit_dashboard_rows()` substitutes `"unknown"` when the value is
  missing/empty.

**One-time header migration (both files):** on the existing ensure/repair path,
if a file's current header line lacks `audit_done_by`, rewrite just the header
line to the new schema. Existing data rows then read back with the new column
empty (→ `"unknown"` at display). New rows are written with the full column set,
keeping DictReader alignment.

## Threading

- `_audit_handler(request, ...)`: `actor = _audit_actor(request)` once; pass to
  `append_audit_dashboard_row(..., audit_done_by=actor)` and
  `trace.save(..., audit_done_by=actor)`.
- `/batch/stream` handler: compute `actor` once from its `request`; pass the same
  value for every offer in the batch.
- Trace-detail re-render (`/traces/{id}/detail`) reads the stored value from the
  trace row — it does not recompute an actor.

## Display

- `/records` (`records.html`): new **"Audit Done By"** column.
- `/traces` list (`traces.html`): new column (from `_row_to_summary`).
- Trace detail (`/traces/{id}`): show `audit_done_by` in the header/summary
  (from `_row_to_full`).
- `result.html`: unchanged — column not shown.

## Error handling / edge cases

- Missing session or missing email → `"admin"` (never an empty actor).
- Old rows (pre-migration) → `"unknown"` at display, never blank.
- `AuditTrace.save` default `"admin"` guards any caller that forgets to pass an
  actor.

## Testing

- Unit (`tests/`): `_audit_actor` — session with email → that email; no session /
  no user / empty email → `"admin"`. Trace row round-trip: `audit_done_by`
  persisted and read back; a row dict missing the key → `"unknown"`.
- Manual: log in, run a single audit and a batch → `/records` and `/traces`
  (list + detail) show your email; `curl -X POST http://localhost:8000/audit`
  from localhost (no session) → shows `admin`; confirm `result.html` does NOT
  show the column.
