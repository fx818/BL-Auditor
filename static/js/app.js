function initTabs() {
  document.querySelectorAll('.tabs').forEach((tabGroup) => {
    tabGroup.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const target = btn.dataset.tab;
        const container = tabGroup.closest('.tab-container') || document;

        tabGroup.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
        container.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.remove('active'));

        btn.classList.add('active');
        const panel = container.querySelector(`#tab-${target}`);
        if (panel) {
          panel.classList.add('active');
        }
      });
    });
  });
}

function initAccordions() {
  document.querySelectorAll('.accordion-header').forEach((header) => {
    header.addEventListener('click', () => {
      header.closest('.accordion')?.classList.toggle('open');
    });
  });
}

function animateTokenBars() {
  document.querySelectorAll('.token-bar-fill[data-width]').forEach((bar) => {
    setTimeout(() => {
      bar.style.width = `${bar.dataset.width}%`;
    }, 100);
  });
}

function initAuditForm() {
  const auditForm = document.getElementById('audit-form');
  if (!auditForm || auditForm.dataset.bound === 'true') {
    return;
  }

  auditForm.dataset.bound = 'true';
  auditForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const overlay = document.getElementById('loading-overlay');
    overlay?.classList.add('active');

    const offerId = document.getElementById('offer-id-input')?.value.trim();
    if (!offerId) {
      overlay?.classList.remove('active');
      showError('Offer ID is required.');
      return;
    }

    try {
      const resp = await fetch(auditForm.dataset.auditUrl || '/audit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          offer_id: offerId,
          selected_agents: window.BLSettings ? Array.from(window.BLSettings.getAgentPrefs()) : null,
        }),
      });

      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        const raw = await resp.text();
        try {
          const body = JSON.parse(raw);
          detail = body.detail || detail;
        } catch (_) {
          if (raw && raw.trim().startsWith('<')) {
            // Server sent an HTML error page with a non-200 status — show it.
            document.open();
            document.write(raw);
            document.close();
            return;
          }
          detail = raw.slice(0, 300) || detail;
        }
        throw new Error(`[${resp.status}] ${detail}`);
      }

      const html = await resp.text();
      document.open();
      document.write(html);
      document.close();
    } catch (err) {
      overlay?.classList.remove('active');
      showError(err.message);
    }
  });
}

function showError(msg, step) {
  let el = document.getElementById('form-error');
  if (!el) {
    el = document.createElement('div');
    el.id = 'form-error';
    el.className = 'error-banner';
    const form = document.getElementById('audit-form');
    if (form) form.parentNode.insertBefore(el, form.nextSibling);
  }
  const title = step ? 'Audit Failed — ' + escapeHtml(step) : 'Audit Failed';
  el.innerHTML =
    '<div class="error-banner-icon">!</div>' +
    '<div class="error-banner-body">' +
      '<div class="error-banner-title">' + title + '</div>' +
      '<div class="error-banner-msg">' + escapeHtml(msg || 'An unexpected error occurred. Check server logs.') + '</div>' +
    '</div>' +
    '<button class="error-banner-close" onclick="this.parentNode.style.display=\'none\'">✕</button>';
  el.style.display = 'flex';
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function copyJson() {
  const text = document.querySelector('.audit-json-viewer')?.textContent
    || document.querySelector('.json-viewer')?.textContent
    || '';
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById('copy-btn');
    if (btn) {
      btn.textContent = 'Copied';
      setTimeout(() => {
        btn.textContent = 'Copy Audit JSON';
      }, 2000);
    }
  });
}

function getVerdictClass(val) {
  if (!val) return '';
  const normalized = val.toString().toLowerCase().replace(/[^a-z_]/g, '_');
  return `verdict verdict-${normalized}`;
}

function goBack() {
  window.location.href = '/';
}

function filterRecordRows() {
  const input = document.getElementById('records-offer-filter');
  const rows = document.querySelectorAll('#records-table .records-row');
  const count = document.getElementById('records-count');
  if (!input || !rows.length) {
    return;
  }

  const query = input.value.trim().toLowerCase();
  let visible = 0;

  rows.forEach((row) => {
    const offerId = (row.dataset.offerId || '').trim().toLowerCase();
    const matches = !query || offerId.includes(query);
    row.style.display = matches ? '' : 'none';
    if (matches) {
      visible += 1;
    }
  });

  if (count) {
    count.textContent = `Showing ${visible} of ${rows.length}`;
  }
}

function initRecordsFilter() {
  const input = document.getElementById('records-offer-filter');
  if (!input || input.dataset.bound === 'true') {
    return;
  }

  input.dataset.bound = 'true';
  input.addEventListener('input', filterRecordRows);
  input.addEventListener('keyup', filterRecordRows);
  filterRecordRows();
}

function downloadTableAsCSV(tableId, filename, options) {
  const table = document.getElementById(tableId);
  if (!table) return;

  const maxRows = (options && options.maxRows) ? parseInt(options.maxRows, 10) : null;
  const colIndices = (options && options.colIndices) ? options.colIndices : null;

  function cellText(el) {
    return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function csvEscape(val) {
    const s = String(val == null ? '' : val);
    if (s.includes(',') || s.includes('"') || s.includes('\n')) {
      return '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
  }

  // Per-<th> attribute overrides:
  //   data-csv-skip="1"  → omit this column from the download entirely
  //   data-csv-name="x"  → use "x" as the CSV header instead of the visible label
  const rows = [];
  const skipIndices = new Set();
  const thead = table.querySelector('thead');
  if (thead) {
    const headerCells = Array.from(thead.querySelectorAll('th'));
    const headerOut = [];
    headerCells.forEach((th, i) => {
      if (th.getAttribute('data-csv-skip') === '1') { skipIndices.add(i); return; }
      if (colIndices && !colIndices.has(i)) { skipIndices.add(i); return; }
      const name = th.getAttribute('data-csv-name');
      headerOut.push(csvEscape(name != null ? name : cellText(th)));
    });
    rows.push(headerOut.join(','));
  }
  let tbodyRows = Array.from(table.querySelectorAll('tbody tr')).filter(
    tr => !tr.hidden && tr.style.display !== 'none'
  );
  if (maxRows && maxRows > 0) tbodyRows = tbodyRows.slice(0, maxRows);
  tbodyRows.forEach(tr => {
    const cells = Array.from(tr.querySelectorAll('td'));
    const out = [];
    cells.forEach((td, i) => {
      if (skipIndices.has(i)) return;
      out.push(csvEscape(cellText(td)));
    });
    rows.push(out.join(','));
  });

  const blob = new Blob([rows.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function openCsvModal(tableId, filename) {
  const table = document.getElementById(tableId);
  if (!table) return;

  // Build column list from thead (skip data-csv-skip columns)
  const thead = table.querySelector('thead');
  const cols = [];
  if (thead) {
    Array.from(thead.querySelectorAll('th')).forEach((th, i) => {
      if (th.getAttribute('data-csv-skip') === '1') return;
      const name = th.getAttribute('data-csv-name');
      const label = name != null ? name : (th.innerText || th.textContent || '').replace(/\s+/g, ' ').trim();
      cols.push({ index: i, label });
    });
  }

  // Backdrop
  const backdrop = document.createElement('div');
  backdrop.className = 'csv-modal-backdrop';

  // Panel
  const panel = document.createElement('div');
  panel.className = 'csv-modal';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'true');

  // Header
  const header = document.createElement('div');
  header.className = 'csv-modal-header';
  header.innerHTML = '<span>Download CSV</span>';
  const closeBtn = document.createElement('button');
  closeBtn.className = 'csv-modal-close';
  closeBtn.innerHTML = '&times;';
  closeBtn.setAttribute('aria-label', 'Close');
  header.appendChild(closeBtn);

  // Row count
  const rowSection = document.createElement('div');
  rowSection.className = 'csv-modal-section';
  rowSection.innerHTML = '<label class="csv-modal-label">Rows</label>';
  const rowInput = document.createElement('input');
  rowInput.type = 'number';
  rowInput.min = '1';
  rowInput.placeholder = 'All rows';
  rowInput.className = 'csv-modal-row-input';
  rowSection.appendChild(rowInput);

  // Column section
  const colSection = document.createElement('div');
  colSection.className = 'csv-modal-section';
  const colHeader = document.createElement('div');
  colHeader.className = 'csv-modal-col-header';
  colHeader.innerHTML = '<span class="csv-modal-label">Columns</span>';
  const selAll = document.createElement('button');
  selAll.className = 'btn btn-outline btn-sm';
  selAll.textContent = 'Select All';
  const deselAll = document.createElement('button');
  deselAll.className = 'btn btn-outline btn-sm';
  deselAll.textContent = 'Deselect All';
  const colBtns = document.createElement('div');
  colBtns.className = 'csv-modal-col-btns';
  colBtns.appendChild(selAll);
  colBtns.appendChild(deselAll);
  colHeader.appendChild(colBtns);
  colSection.appendChild(colHeader);

  const grid = document.createElement('div');
  grid.className = 'csv-modal-cols-grid';
  const checkboxes = cols.map(col => {
    const wrap = document.createElement('label');
    wrap.className = 'csv-modal-check-label';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = true;
    cb.dataset.colIndex = col.index;
    wrap.appendChild(cb);
    wrap.appendChild(document.createTextNode(' ' + col.label));
    grid.appendChild(wrap);
    return cb;
  });
  colSection.appendChild(grid);

  selAll.addEventListener('click', () => checkboxes.forEach(cb => { cb.checked = true; }));
  deselAll.addEventListener('click', () => checkboxes.forEach(cb => { cb.checked = false; }));

  // Footer
  const footer = document.createElement('div');
  footer.className = 'csv-modal-footer';
  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'btn btn-outline btn-sm';
  cancelBtn.textContent = 'Cancel';
  const dlBtn = document.createElement('button');
  dlBtn.className = 'btn btn-primary btn-sm';
  dlBtn.textContent = 'Download';
  footer.appendChild(cancelBtn);
  footer.appendChild(dlBtn);

  panel.appendChild(header);
  panel.appendChild(rowSection);
  panel.appendChild(colSection);
  panel.appendChild(footer);
  backdrop.appendChild(panel);
  document.body.appendChild(backdrop);
  rowInput.focus();

  function close() { document.body.removeChild(backdrop); document.removeEventListener('keydown', onKey); }
  function onKey(e) { if (e.key === 'Escape') close(); }
  document.addEventListener('keydown', onKey);
  backdrop.addEventListener('click', e => { if (e.target === backdrop) close(); });
  closeBtn.addEventListener('click', close);
  cancelBtn.addEventListener('click', close);

  dlBtn.addEventListener('click', () => {
    const maxRows = rowInput.value.trim() ? parseInt(rowInput.value, 10) : null;
    const checked = checkboxes.filter(cb => cb.checked);
    const colIndices = checked.length < checkboxes.length
      ? new Set(checked.map(cb => parseInt(cb.dataset.colIndex, 10)))
      : null;
    close();
    downloadTableAsCSV(tableId, filename, { maxRows, colIndices });
  });
}

function initializePage() {
  initTabs();
  initAccordions();
  animateTokenBars();
  initAuditForm();
  initRecordsFilter();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializePage);
} else {
  initializePage();
}
