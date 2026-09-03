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
