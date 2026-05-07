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
      const resp = await fetch('/audit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ offer_id: offerId }),
      });

      if (!resp.ok) {
        let detail = resp.statusText;
        try {
          const body = await resp.json();
          detail = body.detail || detail;
        } catch (_) {}
        throw new Error(detail);
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

function showError(msg) {
  let el = document.getElementById('form-error');
  if (!el) {
    el = document.createElement('div');
    el.id = 'form-error';
    el.className = 'error-banner';
    const form = document.getElementById('audit-form');
    if (form) form.parentNode.insertBefore(el, form.nextSibling);
  }
  el.innerHTML =
    '<div class="error-banner-icon">!</div>' +
    '<div class="error-banner-body">' +
      '<div class="error-banner-title">Audit Failed</div>' +
      '<div class="error-banner-msg">' + escapeHtml(msg) + '</div>' +
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
