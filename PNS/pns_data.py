import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

API_URL = "http://audio.imutils.com/pns-call-insights"
# API_URL = "http://34.100.140.157/pns-call-insights"

DEFAULT_AK = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMDUyNTMiLCJmdW4iOiIwIiwiZXhwIjoxNzg1OTk2MTMxLCJpYXQiOjE3ODU5MDk3MzEsImlzcyI6IkVNUExPWUVFIn0.GTAVpJdBnaIEgH5xTH265QAwtT6Qf8mwld7CThpgoEg"
DEFAULT_EMP_ID = "105253"


# ─── API ──────────────────────────────────────────────────────────────────────

def _call_single(usr_id, ak, emp_id, modid, page_index, fields):
    payload = {
        "USR_ID": str(usr_id).strip(),
        "AK": ak, "MODID": modid, "EMP_ID": emp_id,
        "PAGE_INDEX": page_index, "FIELDS": fields,
    }
    try:
        resp = requests.post(API_URL, json=payload, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        return {"usr_id": str(usr_id).strip(), "success": True, "code": body.get("Code"), "data": body.get("data", [])}
    except Exception as e:
        return {"usr_id": str(usr_id).strip(), "success": False, "error": str(e), "data": []}


def fetch_pns_data(
    usr_ids,
    ak=DEFAULT_AK,
    emp_id=DEFAULT_EMP_ID,
    modid="PNS",
    page_index=1,
    fields="metadata",
    max_workers=8,
):
    """
    Fetch PNS call insights for one or more USR_IDs.
    usr_ids: str (single or comma-separated), int, or list.
    Returns list of {usr_id, success, data: [{file_id, extracted_data, call_recording_url}]}.
    """
    if isinstance(usr_ids, str):
        ids = [i.strip() for i in usr_ids.split(",") if i.strip()]
    elif isinstance(usr_ids, (int, float)):
        ids = [str(int(usr_ids))]
    else:
        ids = [str(i).strip() for i in usr_ids]

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_call_single, uid, ak, emp_id, modid, page_index, fields): uid for uid in ids}
        for f in as_completed(futures):
            results.append(f.result())

    order = {uid: i for i, uid in enumerate(ids)}
    results.sort(key=lambda r: order.get(r["usr_id"], 9999))
    return results


# ─── Persistence ──────────────────────────────────────────────────────────────

def save_run(results, runs_dir="PNS/runs"):
    Path(runs_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(runs_dir) / f"run_{ts}.json"
    path.write_text(json.dumps({"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "results": results}, indent=2), encoding="utf-8")
    return str(path)


def load_all_runs(runs_dir="PNS/runs"):
    d = Path(runs_dir)
    if not d.exists():
        return []
    runs = []
    for f in sorted(d.glob("run_*.json"), reverse=True):
        try:
            runs.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return runs


# ─── HTML ─────────────────────────────────────────────────────────────────────

_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #f0f2f5; color: #111827; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

.topbar { background: #fff; border-bottom: 1px solid #e5e7eb; padding: 0 20px; height: 52px; display: flex; align-items: center; gap: 10px; flex-shrink: 0; box-shadow: 0 1px 3px rgba(0,0,0,.06); z-index: 10; }
.topbar-logo { font-size: 15px; font-weight: 700; color: #1d4ed8; letter-spacing: -.3px; }
.topbar-sep { color: #d1d5db; }
.topbar-sub { font-size: 12px; color: #6b7280; }
.topbar-stats { margin-left: auto; display: flex; gap: 8px; }
.stat-chip { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 20px; padding: 3px 10px; font-size: 12px; color: #1d4ed8; font-weight: 500; }

.layout { display: flex; flex: 1; overflow: hidden; }

.left { width: 260px; background: #fff; border-right: 1px solid #e5e7eb; display: flex; flex-direction: column; flex-shrink: 0; }
.left-header { padding: 14px 16px 10px; border-bottom: 1px solid #f3f4f6; }
.left-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; color: #9ca3af; margin-bottom: 10px; }
.search-wrap { position: relative; }
.search-wrap input { width: 100%; background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 7px; padding: 7px 10px 7px 32px; font-size: 13px; color: #111827; outline: none; transition: border .15s; }
.search-wrap input:focus { border-color: #93c5fd; background: #fff; box-shadow: 0 0 0 3px #eff6ff; }
.search-icon { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: #9ca3af; font-size: 12px; pointer-events: none; }
.glid-list { flex: 1; overflow-y: auto; padding: 6px; }
.glid-item { display: flex; align-items: center; justify-content: space-between; padding: 9px 10px; border-radius: 7px; cursor: pointer; transition: background .12s; margin-bottom: 2px; }
.glid-item:hover { background: #f3f4f6; }
.glid-item.active { background: #eff6ff; }
.glid-uid { font-size: 13px; font-weight: 600; color: #374151; font-family: 'SF Mono', 'Fira Code', monospace; }
.glid-item.active .glid-uid { color: #1d4ed8; }
.glid-count { font-size: 11px; font-weight: 600; color: #fff; background: #94a3b8; border-radius: 20px; padding: 1px 7px; min-width: 22px; text-align: center; }
.glid-item.active .glid-count { background: #1d4ed8; }
.glid-item.zero .glid-uid { color: #9ca3af; }
.glid-item.zero .glid-count { background: #e5e7eb; color: #9ca3af; }
.glid-empty { padding: 24px 10px; font-size: 13px; color: #9ca3af; text-align: center; }

.right { flex: 1; display: flex; flex-direction: column; overflow: hidden; background: #f0f2f5; }
.placeholder { flex: 1; display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 10px; }
.placeholder-icon { font-size: 36px; opacity: .3; }
.placeholder-text { font-size: 14px; color: #6b7280; }
.placeholder-sub { font-size: 12px; color: #9ca3af; }

.right-content { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.right-header { background: #fff; border-bottom: 1px solid #e5e7eb; padding: 14px 24px; display: flex; align-items: center; gap: 14px; flex-shrink: 0; }
.right-uid { font-size: 18px; font-weight: 700; color: #111827; font-family: 'SF Mono', 'Fira Code', monospace; }
.right-meta { font-size: 12px; color: #6b7280; margin-top: 1px; }

.right-body { flex: 1; overflow-y: auto; padding: 20px 24px; }

.run-group { margin-bottom: 24px; }
.run-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; color: #9ca3af; margin-bottom: 12px; display: flex; align-items: center; gap: 10px; }
.run-label::after { content: ''; flex: 1; height: 1px; background: #e5e7eb; }

.call-card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; margin-bottom: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.05); }
.call-top { padding: 14px 16px; }
.call-row1 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 7px; }
.file-id { font-size: 11px; font-family: monospace; color: #6b7280; background: #f3f4f6; border: 1px solid #e5e7eb; padding: 2px 7px; border-radius: 4px; }
.badge { display: inline-block; padding: 2px 9px; border-radius: 5px; font-size: 12px; font-weight: 600; }
.badge-b2b { background: #dbeafe; color: #1d4ed8; }
.badge-b2c { background: #ffedd5; color: #c2410c; }
.badge-high { background: #dcfce7; color: #15803d; }
.badge-med { background: #fef9c3; color: #92400e; }
.badge-low { background: #fee2e2; color: #b91c1c; }
.badge-purple { background: #ede9fe; color: #6d28d9; }
.call-row2 { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 7px; }
.meta-dim { font-size: 12px; color: #6b7280; }
.kw { background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 3px; padding: 1px 6px; font-size: 11px; color: #475569; }
.reason { font-size: 12px; color: #6b7280; font-style: italic; line-height: 1.6; }
.rec-link { color: #1d4ed8; text-decoration: none; font-size: 12px; padding: 3px 9px; border: 1px solid #bfdbfe; border-radius: 4px; white-space: nowrap; margin-left: auto; }
.rec-link:hover { background: #eff6ff; }

.card-section { padding: 10px 16px; border-top: 1px solid #f3f4f6; }
.section-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .09em; color: #9ca3af; margin-bottom: 7px; }
.intent-row { display: flex; gap: 8px; align-items: flex-start; flex-wrap: wrap; margin-bottom: 5px; }
.q-table { width: 100%; border-collapse: collapse; }
.q-table th { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: #9ca3af; text-align: left; padding: 5px 8px; border-bottom: 1px solid #f3f4f6; }
.q-table td { padding: 6px 8px; border-bottom: 1px solid #f9fafb; font-size: 12px; vertical-align: top; color: #374151; }
.answer-cell { color: #059669; font-style: italic; }
.q-table tr:last-child td { border-bottom: none; }

.no-calls { padding: 14px 0; font-size: 13px; color: #9ca3af; font-style: italic; }

.raw-footer { background: #fff; border-top: 2px solid #e5e7eb; flex-shrink: 0; }
.raw-toggle-btn { width: 100%; display: flex; align-items: center; justify-content: space-between; padding: 11px 24px; cursor: pointer; border: none; background: none; font-size: 13px; font-weight: 600; color: #374151; transition: background .15s; text-align: left; }
.raw-toggle-btn:hover { background: #f9fafb; }
.raw-label { display: flex; align-items: center; gap: 8px; }
.raw-arrow { font-size: 10px; color: #9ca3af; transition: transform .2s; display: inline-block; }
.raw-arrow.open { transform: rotate(180deg); }
.raw-body { display: none; padding: 0 24px 16px; max-height: 360px; overflow-y: auto; }
.raw-body.open { display: block; }
pre.raw-json { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; font-size: 11px; font-family: 'JetBrains Mono', 'Fira Code', monospace; color: #334155; white-space: pre; overflow-x: auto; line-height: 1.65; }
"""

_JS = r"""
const GLIDS = __GLIDS_DATA__;
let activeIdx = null;

function init() {
  renderList(GLIDS);
  document.getElementById('search').addEventListener('input', function() {
    const q = this.value.trim().toLowerCase();
    renderList(q ? GLIDS.filter(g => g.usr_id.includes(q)) : GLIDS);
  });
}

function renderList(items) {
  const el = document.getElementById('glid-list');
  if (!items.length) {
    el.innerHTML = '<div class="glid-empty">No GLIDs found</div>';
    return;
  }
  el.innerHTML = items.map(g => {
    const idx = GLIDS.indexOf(g);
    const total = g.runs.reduce((s, r) => s + r.records.length, 0);
    return `<div class="glid-item ${total === 0 ? 'zero' : ''} ${activeIdx === idx ? 'active' : ''}" onclick="selectGlid(${idx})">
      <span class="glid-uid">${g.usr_id}</span>
      <span class="glid-count">${total}</span>
    </div>`;
  }).join('');
}

function selectGlid(idx) {
  activeIdx = idx;
  const q = document.getElementById('search').value.trim().toLowerCase();
  renderList(q ? GLIDS.filter(g => g.usr_id.includes(q)) : GLIDS);
  renderDetail(GLIDS[idx]);
}

function renderDetail(g) {
  const total = g.runs.reduce((s, r) => s + r.records.length, 0);
  document.getElementById('placeholder').style.display = 'none';
  const rc = document.getElementById('right-content');
  rc.style.display = 'flex';
  document.getElementById('detail-uid').textContent = g.usr_id;
  document.getElementById('detail-meta').textContent =
    `${total} call${total !== 1 ? 's' : ''} · ${g.runs.length} run${g.runs.length !== 1 ? 's' : ''}`;

  const body = document.getElementById('detail-body');
  body.innerHTML = g.runs.map(run => {
    let cards = '';
    if (!run.success) {
      cards = `<div class="no-calls">Error: ${run.error}</div>`;
    } else if (!run.records.length) {
      cards = `<div class="no-calls">No call data returned for this run</div>`;
    } else {
      cards = run.records.map(renderCard).join('');
    }
    return `<div class="run-group"><div class="run-label">${run.ts}</div>${cards}</div>`;
  }).join('');

  // Reset and populate raw footer
  document.getElementById('raw-body').classList.remove('open');
  document.getElementById('raw-arrow').classList.remove('open');
  const rawData = g.runs.map(r => ({ run: r.ts, records: r.records }));
  document.getElementById('raw-json-pre').textContent = JSON.stringify(rawData, null, 2);
}

function renderCard(rec) {
  const ext = rec.extracted_data || {};
  const ct = ext.call_type || {};
  const bi = ext.buyer_intent || null;
  const bc = ext.buyer_conclusion || null;
  const ad = ext.additional_details || null;
  const ev = ct.evidence || {};

  const ctType = ct.type || '?';
  const ctBadge = `<span class="badge ${ctType === 'B2B' ? 'badge-b2b' : 'badge-b2c'}">${ctType}</span>`;

  const kws = (ev.keywords || []).map(k => `<span class="kw">${esc(k)}</span>`).join(' ');
  const recUrl = rec.call_recording_url || '';
  const recLink = recUrl ? `<a href="${recUrl}" target="_blank" class="rec-link">&#9654; Recording</a>` : '';
  const appNote = ext.intended_application ? `<span class="meta-dim">· ${esc(ext.intended_application)}</span>` : '';
  const repeatNote = 'repeat_buyer' in ev ? `<span class="meta-dim">· Repeat: ${ev.repeat_buyer}</span>` : '';

  let intentSec = '';
  if (bi) {
    const lvl = bi.intent_level || '';
    const cls = lvl === 'High' ? 'badge-high' : lvl === 'Medium' ? 'badge-med' : 'badge-low';
    intentSec = `<div class="card-section">
      <div class="section-label">Buyer Intent</div>
      <div class="intent-row">
        <span class="badge ${cls}">${lvl}</span>
        <span class="meta-dim">${esc(bi.narrative || '')}</span>
      </div>
      <div class="meta-dim" style="font-size:12px;margin-top:4px">${esc(bi.reasoning || '')}</div>
    </div>`;
  }

  let conclusionSec = '';
  if (bc) {
    conclusionSec = `<div class="card-section">
      <div class="section-label">Buyer Conclusion</div>
      <span class="badge badge-purple">${esc(bc.category || '')}</span>
      <span class="meta-dim"> — ${esc(bc.conclusion_notes || '')}</span>
    </div>`;
  }

  let queriesSec = '';
  if (ad) {
    const sellerQs = (ad.seller_queries || []).map(q => [q, 'Seller']);
    const buyerQs  = (ad.buyer_queries  || []).map(q => [q, 'Buyer']);
    const allQs = [...sellerQs, ...buyerQs].sort((a, b) => (a[0].sequence || 0) - (b[0].sequence || 0));
    if (allQs.length) {
      const rows = allQs.map(([q, side]) => {
        const answer = side === 'Seller' ? (q.answer_by_buyer || '') : (q.answer_by_seller || '');
        return `<tr>
          <td style="color:${side === 'Seller' ? '#1d4ed8' : '#c2410c'};font-size:11px;font-weight:600">${side}</td>
          <td class="meta-dim">${esc(q.category || '')}</td>
          <td>${esc(q.query || '')}</td>
          <td class="answer-cell">${esc(answer)}</td>
        </tr>`;
      }).join('');
      queriesSec = `<div class="card-section">
        <div class="section-label">Queries</div>
        <table class="q-table"><thead><tr><th>Side</th><th>Category</th><th>Query</th><th>Answer</th></tr></thead><tbody>${rows}</tbody></table>
      </div>`;
    }
  }

  return `<div class="call-card">
    <div class="call-top">
      <div class="call-row1">
        <span class="file-id">#${rec.file_id || ''}</span>
        ${ctBadge}
        <span class="meta-dim">${esc(ext.call_purpose || '')}</span>
        <span class="meta-dim">· ${esc(ext.primary_language || '')} (${(ext.all_languages || []).map(esc).join(', ')})</span>
        ${appNote}
        ${recLink}
      </div>
      <div class="call-row2">
        <span class="meta-dim">Persona: <strong>${esc(ev.buyer_persona || '')}</strong></span>
        <span class="meta-dim">· Order: ${esc(ev.order_type || '')}</span>
        <span class="meta-dim">· Qty: ${esc(ev.quantity_scale || '')}</span>
        ${repeatNote}
        ${kws}
      </div>
      <div class="reason">${esc(ct.reason || '')}</div>
    </div>
    ${intentSec}${conclusionSec}${queriesSec}
  </div>`;
}

function toggleRaw() {
  document.getElementById('raw-body').classList.toggle('open');
  document.getElementById('raw-arrow').classList.toggle('open');
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

document.addEventListener('DOMContentLoaded', init);
"""


def _build_glid_data(all_runs):
    # all_runs is newest-first; first occurrence of a GLID wins (overrides older runs)
    glid_map = {}
    for run in all_runs:
        ts = run.get("timestamp", "")
        for r in run.get("results", []):
            uid = r["usr_id"]
            if uid not in glid_map:
                glid_map[uid] = {"usr_id": uid, "runs": [{
                    "ts": ts,
                    "records": r.get("data", []),
                    "success": r.get("success", True),
                    "error": r.get("error", ""),
                }]}
    glids = list(glid_map.values())
    glids.sort(key=lambda g: sum(len(r["records"]) for r in g["runs"]), reverse=True)
    return glids


def generate_html(results, output_path="PNS/pns_report.html", runs_dir=None, auto_save=True):
    """
    Generate a self-contained single-page HTML report.
    All run history embedded as JSON — no server needed.
    Returns output_path.
    """
    if runs_dir is None:
        runs_dir = str(Path(output_path).parent / "runs")

    if auto_save:
        save_run(results, runs_dir)

    all_runs = load_all_runs(runs_dir)
    if not all_runs:
        all_runs = [{"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "results": results}]

    glids = _build_glid_data(all_runs)
    total_calls = sum(sum(len(r["records"]) for r in g["runs"]) for g in glids)
    glids_with_data = sum(1 for g in glids if any(r["records"] for r in g["runs"]))

    js = _JS.replace("__GLIDS_DATA__", json.dumps(glids, ensure_ascii=False))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PNS Call Insights</title>
<style>{_CSS}</style>
</head>
<body>
<div class="topbar">
  <span class="topbar-logo">PNS</span>
  <span class="topbar-sep">/</span>
  <span class="topbar-sub">Call Insights</span>
  <div class="topbar-stats">
    <span class="stat-chip">{len(all_runs)} run{"s" if len(all_runs) != 1 else ""}</span>
    <span class="stat-chip">{glids_with_data} GLIDs with data</span>
    <span class="stat-chip">{total_calls} calls</span>
  </div>
</div>
<div class="layout">
  <div class="left">
    <div class="left-header">
      <div class="left-title">GLIDs</div>
      <div class="search-wrap">
        <span class="search-icon">&#128269;</span>
        <input id="search" type="text" placeholder="Search GLID...">
      </div>
    </div>
    <div class="glid-list" id="glid-list"></div>
  </div>
  <div class="right">
    <div id="placeholder" class="placeholder">
      <div class="placeholder-icon">&#9742;</div>
      <div class="placeholder-text">Select a GLID to view call insights</div>
      <div class="placeholder-sub">{len(glids)} GLIDs &middot; {len(all_runs)} run{"s" if len(all_runs) != 1 else ""} &middot; {total_calls} calls</div>
    </div>
    <div id="right-content" class="right-content" style="display:none">
      <div class="right-header">
        <div>
          <div id="detail-uid" class="right-uid"></div>
          <div id="detail-meta" class="right-meta"></div>
        </div>
      </div>
      <div id="detail-body" class="right-body"></div>
      <div class="raw-footer">
        <button class="raw-toggle-btn" onclick="toggleRaw()">
          <span class="raw-label">&#123;&#125; Raw API Response</span>
          <span class="raw-arrow" id="raw-arrow">&#9650;</span>
        </button>
        <div class="raw-body" id="raw-body">
          <pre class="raw-json" id="raw-json-pre"></pre>
        </div>
      </div>
    </div>
  </div>
</div>
<script>{js}</script>
</body>
</html>"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(html, encoding="utf-8")
    return output_path


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m PNS.pns_data <GLID1,GLID2,...> [output.html]")
        print("Example: python -m PNS.pns_data 36810708,14342326,101383559")
        sys.exit(1)

    usr_ids_input = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "PNS/pns_report.html"

    ids = [i.strip() for i in usr_ids_input.split(",") if i.strip()]
    print(f"Fetching for {len(ids)} GLID{'s' if len(ids) != 1 else ''}...")
    results = fetch_pns_data(usr_ids_input)

    total = sum(len(r["data"]) for r in results)
    empty = sum(1 for r in results if not r["data"] and r["success"])
    errors = sum(1 for r in results if not r["success"])
    print(f"Done — {total} calls, {empty} empty, {errors} errors")

    path = generate_html(results, out)
    print(f"Report: {path}")
