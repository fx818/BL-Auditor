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
