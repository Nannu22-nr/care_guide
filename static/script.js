// =====================================================================
// Care Guide - Document Intelligence UI (vanilla JS, served by FastAPI)
// =====================================================================
let selectedFiles = [];
let pollInterval = null;
let currentSelectedJobId = null;
let pageOffset = 0;
const PAGE_SIZE = 20;

// search state
let searchActive = false;

const FIELDS = ['invoice_number', 'invoice_date', 'supplier_name', 'customer_name',
                'subtotal', 'tax', 'total', 'currency'];
const STAGE_PROGRESS = {
  queued: 5, extracting_text: 30, llm_extraction: 65, validating: 85, done: 100, error: 100,
};

const $ = (id) => document.getElementById(id);
const dropzone = $('dropzone');
const fileInput = $('fileInput');
const fileList = $('fileList');
const uploadBtn = $('uploadBtn');
const jobsList = $('jobsList');
const detailCard = $('detailCard');

// ---------------------------------------------------------------------
// API key handling (only used when the server has API_KEY configured)
// ---------------------------------------------------------------------
const getKey = () => sessionStorage.getItem('apiKey') || '';

function askForKey(msg) {
  const k = prompt(msg || 'This server requires an API key:', getKey());
  if (k !== null) sessionStorage.setItem('apiKey', k.trim());
  return getKey();
}

// URL for <img>/<iframe>/<a> which cannot send custom headers
function withKey(url) {
  const k = getKey();
  return k ? `${url}${url.includes('?') ? '&' : '?'}api_key=${encodeURIComponent(k)}` : url;
}

async function apiFetch(url, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const k = getKey();
  if (k) headers['X-API-Key'] = k;
  let res = await fetch(url, Object.assign({}, opts, { headers }));
  if (res.status === 401) {
    const nk = askForKey('Invalid or missing API key. Enter the API key:');
    if (nk) {
      headers['X-API-Key'] = nk;
      res = await fetch(url, Object.assign({}, opts, { headers }));
    }
  }
  return res;
}

// ---------------------------------------------------------------------
// Health + config
// ---------------------------------------------------------------------
fetch('/api/health').then(r => r.json()).then(d => {
  const b = $('healthBadge');
  b.textContent = d.status === 'ok' ? '● System online' : '● Degraded';
  b.classList.add(d.status === 'ok' ? 'ok' : 'degraded');
}).catch(() => { $('healthBadge').textContent = '● Backend offline'; });

fetch('/api/config').then(r => r.json()).then(cfg => {
  if (cfg.auth_required) {
    const btn = $('apiKeyBtn');
    btn.style.display = '';
    btn.onclick = () => { askForKey(); loadJobs(); };
  }
}).catch(() => {});

// ---------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------
dropzone.addEventListener('click', () => fileInput.click());
dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', (e) => {
  e.preventDefault(); dropzone.classList.remove('dragover');
  addFiles(e.dataTransfer.files);
});
fileInput.addEventListener('change', () => addFiles(fileInput.files));

function addFiles(list) {
  for (const f of list) {
    if (!selectedFiles.find(x => x.name === f.name && x.size === f.size)) selectedFiles.push(f);
  }
  renderFileList();
  fileInput.value = '';
}

function renderFileList() {
  fileList.innerHTML = '';
  selectedFiles.forEach((f, i) => {
    const div = document.createElement('div');
    div.style.cssText = "display:flex; align-items:center; gap:8px; background:#fff; padding:4px 10px; border-radius:6px; border:1px solid var(--border); font-size:0.8rem;";
    div.innerHTML = `
      <span class="name">${escapeHtml(f.name)}</span>
      <button class="btn ghost small" style="padding:2px 6px; color:var(--error); border-color:transparent;">✕</button>
    `;
    div.querySelector('button').onclick = () => {
      selectedFiles.splice(i, 1); renderFileList();
    };
    fileList.appendChild(div);
  });
  uploadBtn.disabled = selectedFiles.length === 0;
}

uploadBtn.addEventListener('click', async () => {
  if (!selectedFiles.length) return;
  uploadBtn.disabled = true;
  uploadBtn.textContent = 'Uploading…';
  const fd = new FormData();
  selectedFiles.forEach(f => fd.append('files', f));
  try {
    const res = await apiFetch('/api/upload', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    const data = await res.json();
    const results = data.results || [];
    const queued = results.filter(r => r.job_id && !r.error && !r.duplicate_of).length;
    const dups = results.filter(r => r.duplicate_of).length;
    const errs = results.filter(r => r.error);
    let msg = `Queued ${queued} file(s)`;
    if (dups) msg += `, ${dups} already uploaded`;
    if (errs.length) msg += `, ${errs.length} rejected (${errs.map(e => `${e.filename}: ${e.error}`).join('; ')})`;
    toast(msg, errs.length ? 'error' : 'success');

    selectedFiles = []; renderFileList();
    clearSearch(false);
    pageOffset = 0; loadJobs(); startPolling();
  } catch (e) {
    toast('Upload failed: ' + e.message, 'error');
  } finally {
    uploadBtn.textContent = 'Process Files';
    uploadBtn.disabled = selectedFiles.length === 0;
  }
});

// ---------------------------------------------------------------------
// Jobs list (left)
// ---------------------------------------------------------------------
async function loadJobs() {
  if (searchActive) return;   // do not overwrite search results
  try {
    const res = await apiFetch(`/api/jobs?limit=${PAGE_SIZE}&offset=${pageOffset}`);
    const data = await res.json();
    renderJobs(data.jobs || [], jobsList);

    $('pageInfo').textContent = `Page ${Math.floor(pageOffset / PAGE_SIZE) + 1}`;
    $('prevPage').disabled = pageOffset === 0;
    $('nextPage').disabled = (pageOffset + PAGE_SIZE) >= (data.total || 0);
    return data;
  } catch (e) { /* ignore */ }
}

function renderJobs(jobs, target) {
  if (!jobs.length) {
    target.innerHTML = '<p class="muted center">No documents found.</p>';
    return;
  }
  target.innerHTML = '';
  jobs.forEach(j => {
    const row = document.createElement('div');
    row.className = 'job-row';
    if (currentSelectedJobId === j.id) row.classList.add('active');

    const t = new Date(j.created_at * 1000).toLocaleString();
    const stage = ['queued', 'processing'].includes(j.status) && j.stage ? ` · ${escapeHtml(j.stage.replace('_', ' '))}` : '';

    row.innerHTML = `
      <div class="job-main">
        <div class="job-name">${escapeHtml(j.filename)}</div>
        <div class="job-meta">${t}${j.processing_time_sec ? ' · ' + Number(j.processing_time_sec).toFixed(2) + 's' : ''}${stage}</div>
      </div>
      <div style="display:flex; align-items:center; gap:8px; flex-shrink: 0;">
        <span class="pill ${escapeHtml(j.status)}">${escapeHtml(j.status.replace('_', ' '))}</span>
        <button class="btn ghost small delete-btn" style="color:var(--error); border-color:transparent; padding:4px 8px;" title="Delete">🗑</button>
      </div>
    `;

    row.onclick = (e) => {
      if (e.target.closest('.delete-btn')) return;
      document.querySelectorAll('.job-row').forEach(r => r.classList.remove('active'));
      row.classList.add('active');
      loadJobDetail(j.id);
    };

    row.querySelector('.delete-btn').onclick = async (e) => {
      e.stopPropagation();
      if (!confirm("Are you sure you want to delete this document?")) return;
      try {
        const res = await apiFetch(`/api/jobs/${j.id}`, { method: 'DELETE' });
        if (res.ok) {
          toast('Document deleted', 'success');
          if (currentSelectedJobId === j.id) resetDetail();
          refreshList();
        } else {
          toast('Failed to delete', 'error');
        }
      } catch (err) {
        toast('Error deleting document', 'error');
      }
    };

    target.appendChild(row);
  });
}

function resetDetail() {
  currentSelectedJobId = null;
  detailCard.className = 'card empty-state';
  detailCard.innerHTML = `
    <div class="empty-icon">📄</div>
    <h3>No Document Selected</h3>
    <p class="muted">Select a document from the left list to view its extracted details.</p>
  `;
}

function refreshList() { return searchActive ? runSearch() : loadJobs(); }

$('refreshBtn').onclick = refreshList;
$('prevPage').onclick = () => { if (pageOffset >= PAGE_SIZE) { pageOffset -= PAGE_SIZE; loadJobs(); } };
$('nextPage').onclick = () => { pageOffset += PAGE_SIZE; loadJobs(); };

// ---------------------------------------------------------------------
// Polling: keeps list + the open document's progress up to date
// ---------------------------------------------------------------------
function startPolling() {
  if (pollInterval) return;
  pollInterval = setInterval(async () => {
    try {
      const res = await apiFetch(`/api/jobs?limit=${PAGE_SIZE}&offset=${pageOffset}`);
      const data = await res.json();
      const jobs = data.jobs || [];
      if (!searchActive) renderJobs(jobs, jobsList);

      // live progress of the document that is open on the right
      if (currentSelectedJobId) {
        const open = jobs.find(j => j.id === currentSelectedJobId);
        if (open && ['queued', 'processing'].includes(open.status)) loadJobDetail(open.id, true);
      }

      const active = jobs.some(j => ['queued', 'processing'].includes(j.status));
      if (!active) {
        clearInterval(pollInterval); pollInterval = null;
        if (currentSelectedJobId) loadJobDetail(currentSelectedJobId, true);
      }
    } catch (e) { /* keep polling */ }
  }, 2500);
}

// ---------------------------------------------------------------------
// Search (keyword / semantic + status filter)
// ---------------------------------------------------------------------
async function runSearch() {
  const q = $('searchInput').value.trim();
  const mode = $('searchMode').value;
  const status = $('searchStatus').value;
  if (!q && !status) { clearSearch(true); return; }

  searchActive = true;
  $('searchClear').style.display = '';
  document.querySelector('.pager').style.display = 'none';

  const params = new URLSearchParams({ q, mode, limit: '50' });
  if (status) params.set('status', status);
  try {
    const res = await apiFetch(`/api/search?${params}`);
    const data = await res.json();
    renderJobs(data.jobs || [], jobsList);
    const info = $('searchInfo');
    info.style.display = '';
    info.textContent = `${(data.jobs || []).length} result(s) · ${data.mode} search` +
      (data.note ? ` · ${data.note}` : '');
  } catch (e) {
    toast('Search failed: ' + e.message, 'error');
  }
}

function clearSearch(reload = true) {
  searchActive = false;
  $('searchInput').value = '';
  $('searchStatus').value = '';
  $('searchClear').style.display = 'none';
  $('searchInfo').style.display = 'none';
  document.querySelector('.pager').style.display = '';
  if (reload) loadJobs();
}

$('searchBtn').onclick = runSearch;
$('searchClear').onclick = () => clearSearch(true);
$('searchInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') runSearch(); });
$('searchStatus').addEventListener('change', runSearch);

// ---------------------------------------------------------------------
// Job detail (right)
// ---------------------------------------------------------------------
const sectionTitle = (t, extra = '') =>
  `<h3 style="font-size:0.85rem; text-transform:uppercase; color:var(--muted); margin-bottom:12px;">${t}${extra}</h3>`;

function renderValidation(validation) {
  if (!validation) return '';
  const errors = validation.errors || [];
  const flags = validation.flags || [];
  if (!errors.length && !flags.length) {
    return `<div class="detail-section" style="margin-bottom:24px;">
      ${sectionTitle('Validation')}
      <div style="padding:10px 12px; border-radius:8px; background:#dcfce7; color:#166534; font-size:0.9rem;">✓ All checks passed</div>
    </div>`;
  }
  const row = (it) => {
    const sev = it.severity || 'INFO';
    const color = sev === 'ERROR' ? ['#fee2e2', '#b91c1c'] : sev === 'WARN' ? ['#fef3c7', '#b45309'] : ['#e0f2fe', '#0369a1'];
    return `<div style="padding:8px 12px; border-radius:8px; background:${color[0]}; color:${color[1]}; font-size:0.85rem; margin-bottom:6px;">
      <strong>${escapeHtml(sev)} · ${escapeHtml(it.rule)}</strong>${it.field ? ` <span style="opacity:.8">(${escapeHtml(it.field)})</span>` : ''}
      <div>${escapeHtml(it.message || '')}</div>
    </div>`;
  };
  return `<div class="detail-section" style="margin-bottom:24px;">
    ${sectionTitle('Validation', ` · ${errors.length} error(s), ${flags.length} flag(s)`)}
    ${errors.map(row).join('')}${flags.map(row).join('')}
  </div>`;
}

function renderHistory(events) {
  if (!events || !events.length) return '';
  const rows = events.map(ev => {
    const t = new Date(ev.created_at * 1000).toLocaleTimeString();
    return `<div style="font-size:0.8rem; padding:4px 0; border-bottom:1px dashed var(--border);">
      <span class="muted">${t}</span> · <strong>${escapeHtml(ev.stage)}</strong>
      ${ev.status ? `<span class="muted">[${escapeHtml(ev.status)}]</span>` : ''}
      ${ev.message ? ` — ${escapeHtml(ev.message)}` : ''}
    </div>`;
  }).join('');
  return `<div class="detail-section" style="margin-bottom:24px;">
    ${sectionTitle('Processing History')}${rows}</div>`;
}

async function loadJobDetail(jobId, silent = false) {
  currentSelectedJobId = jobId;
  detailCard.className = 'card';
  if (!silent) detailCard.innerHTML = '<p class="muted center">Loading details…</p>';

  try {
    const res = await apiFetch(`/api/jobs/${jobId}`);
    if (!res.ok) { detailCard.innerHTML = '<p class="muted">Not found.</p>'; return; }
    const job = await res.json();

    const ext = job.extraction || {};
    const corrections = job.corrections || [];
    const active = ['queued', 'processing'].includes(job.status);
    let html = '';

    html += `<div class="detail-header">
      <div>
        <h2>${escapeHtml(job.filename)}</h2>
        <div style="display:flex; align-items:center; gap:12px; margin-top:8px; flex-wrap:wrap;">
          <span class="pill ${escapeHtml(job.status)}">${escapeHtml(job.status.replace('_', ' '))}</span>
          <span class="muted">Stage: ${escapeHtml(job.stage || '—')}</span>
          <span class="muted">${job.processing_time_sec ? Number(job.processing_time_sec).toFixed(2) + 's' : ''}</span>
        </div>
      </div>
      <div style="display:flex; gap:8px;">
        <button class="btn ghost small" id="reprocessBtn" ${active ? 'disabled' : ''}>↻ Reprocess</button>
        <button class="btn ghost small" id="deleteBtn" style="color:var(--error); border-color:rgba(239,68,68,0.3);">🗑 Delete</button>
      </div>
    </div>`;

    if (active) {
      const pct = STAGE_PROGRESS[job.stage] ?? 10;
      html += `<div class="detail-section" style="margin-bottom:24px;">
        <div style="height:8px; background:#e2e8f0; border-radius:4px; overflow:hidden;">
          <div style="height:100%; width:${pct}%; background:var(--primary); transition:width .4s;"></div>
        </div>
        <p class="muted" style="margin-top:8px">${escapeHtml((job.stage || 'queued').replace('_', ' '))}… (${pct}%)</p>
      </div>`;
    }

    if (job.status === 'failed' && job.error) {
      html += `<div style="padding:10px 12px; border-radius:8px; background:#fee2e2; color:#b91c1c; font-size:0.9rem; margin-bottom:20px;">
        <strong>Processing failed:</strong> ${escapeHtml(job.error)}<br>
        <span style="font-size:0.8rem">Use “Reprocess” to retry.</span>
      </div>`;
    }

    html += `<div class="split-view">`;

    // ---- left: preview ----
    html += `<div><div class="detail-section">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
        ${sectionTitle('Document Preview').replace('margin-bottom:12px;', 'margin-bottom:0;')}
        <a href="${withKey('/api/download/' + job.id)}" class="btn ghost small" download>⬇ Download</a>
      </div>
      <div class="doc-preview" id="docPreview"></div>
    </div></div>`;

    // ---- right: validation, fields, items, correction, history ----
    html += `<div>`;
    html += renderValidation(job.validation);

    if (ext && Object.keys(ext).length) {
      html += `<div class="detail-section" style="margin-bottom:24px;">${sectionTitle('Extracted Fields', ' <span class="muted" style="text-transform:none">(click a value to edit)</span>')}<div class="field-grid" id="fieldGrid">`;
      FIELDS.forEach(f => {
        const v = ext[f];
        const isNull = v === null || v === undefined || v === '';
        const conf = (ext.confidence && ext.confidence[f] != null) ? ext.confidence[f] : null;
        html += `<div class="field" data-field="${f}" data-value="${isNull ? '' : escapeHtml(String(v))}">
          <div class="field-label">${f}${conf != null ? ' · ' + (conf * 100).toFixed(0) + '%' : ''}</div>
          <div class="field-value ${isNull ? 'null' : ''}">${isNull ? 'null' : escapeHtml(String(v))}</div>
        </div>`;
      });
      html += `</div></div>`;

      if (Array.isArray(ext.line_items) && ext.line_items.length) {
        html += `<div class="detail-section" style="margin-bottom:24px;">${sectionTitle(`Line Items (${ext.line_items.length})`)}
          <div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; font-size:0.85rem;">
            <thead><tr style="text-align:left; color:var(--muted);">
              <th style="padding:6px">#</th><th style="padding:6px">Description</th>
              <th style="padding:6px">Qty</th><th style="padding:6px">Unit price</th><th style="padding:6px">Amount</th>
            </tr></thead><tbody>`;
        ext.line_items.forEach((it, i) => {
          html += `<tr style="border-top:1px solid var(--border);">
            <td style="padding:6px">${i + 1}</td>
            <td style="padding:6px">${escapeHtml(it.description ?? '—')}</td>
            <td style="padding:6px">${escapeHtml(it.quantity ?? '—')}</td>
            <td style="padding:6px">${escapeHtml(it.unit_price ?? '—')}</td>
            <td style="padding:6px">${escapeHtml(it.amount ?? '—')}</td></tr>`;
        });
        html += `</tbody></table></div></div>`;
      }

      html += `<div class="detail-section" style="margin-bottom:24px;">${sectionTitle('Add Correction')}
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <select id="corrField" class="field-input" style="flex:1; min-width:140px; padding:8px;">
            ${FIELDS.map(f => `<option value="${f}">${f}</option>`).join('')}
          </select>
          <input id="corrValue" class="field-input" style="flex:2; min-width:180px; padding:8px;" placeholder="New value" />
          <button class="btn primary small" id="corrBtn">Save</button>
        </div>
      </div>`;
    }

    if (corrections.length) {
      html += `<div class="detail-section" style="margin-bottom:24px;">${sectionTitle('Correction History')}<div style="font-size:0.85rem;">`;
      corrections.forEach(c => {
        let ov = c.old_value, nv = c.new_value;
        try { ov = JSON.parse(c.old_value); } catch {}
        try { nv = JSON.parse(c.new_value); } catch {}
        html += `<div style="padding:10px; background:var(--bg); border-radius:6px; margin-bottom:6px; border:1px solid var(--border);">
          <span style="font-weight:700; color:var(--primary);">${escapeHtml(c.field)}</span>:
          ${escapeHtml(String(ov))} → <strong>${escapeHtml(String(nv))}</strong>
        </div>`;
      });
      html += `</div></div>`;
    }

    html += renderHistory(job.events);
    html += `</div></div>`; // close right column + split-view

    detailCard.innerHTML = html;
    renderDocPreview(job.id, job.filename);

    document.querySelectorAll('#fieldGrid .field').forEach(el => {
      el.onclick = () => inlineEdit(el, job.id);
    });

    const corrBtn = $('corrBtn');
    if (corrBtn) {
      corrBtn.onclick = async () => {
        const field = $('corrField').value;
        const value = $('corrValue').value;
        if (!value) { toast('Enter a value', 'error'); return; }
        await submitCorrection(job.id, field, value);
      };
    }

    $('deleteBtn').onclick = async () => {
      if (!confirm("Are you sure you want to delete this document?")) return;
      const r = await apiFetch(`/api/jobs/${job.id}`, { method: 'DELETE' });
      if (r.ok) { toast('Document deleted', 'success'); resetDetail(); refreshList(); }
      else toast('Failed to delete document', 'error');
    };

    $('reprocessBtn').onclick = async () => {
      const r = await apiFetch(`/api/jobs/${job.id}/reprocess`, { method: 'POST' });
      if (r.ok) { toast('Re-queued', 'success'); loadJobDetail(job.id); startPolling(); refreshList(); }
      else toast('Could not reprocess (already running?)', 'error');
    };
  } catch (e) {
    detailCard.innerHTML = `<p class="muted" style="color:var(--error)">Error loading details: ${escapeHtml(e.message)}</p>`;
  }
}

async function submitCorrection(jobId, field, value) {
  const res = await apiFetch(`/api/jobs/${jobId}/correct`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ field, value })
  });
  if (res.ok) {
    toast(`Updated ${field}`, 'success');
    loadJobDetail(jobId); refreshList();
  } else {
    let msg = 'Correction failed';
    try { const d = await res.json(); if (d.detail) msg += ': ' + (typeof d.detail === 'string' ? d.detail : 'invalid input'); } catch {}
    toast(msg, 'error');
    loadJobDetail(jobId);
  }
}

function renderDocPreview(jobId, filename) {
  const el = $('docPreview');
  if (!el) return;
  const ext = (filename.split('.').pop() || '').toLowerCase();
  const url = withKey(`/api/files/${jobId}`);

  if (ext === 'pdf') {
    el.innerHTML = `<iframe src="${escapeHtml(url)}#toolbar=0" style="width:100%; height:100%; min-height:500px; border:none;"></iframe>`;
  } else if (['jpg', 'jpeg', 'png'].includes(ext)) {
    el.innerHTML = `<img src="${escapeHtml(url)}" alt="preview" style="max-width:100%; max-height:100%; object-fit:contain;" />`;
  } else {
    el.innerHTML = `<p class="muted">Preview not available for ${escapeHtml(ext.toUpperCase())} files</p>`;
  }
}

function inlineEdit(fieldEl, jobId) {
  const field = fieldEl.dataset.field;
  const current = fieldEl.dataset.value;
  fieldEl.innerHTML = `
    <div class="field-label">${escapeHtml(field)}</div>
    <input class="field-edit-input" value="${escapeHtml(current)}" />
  `;
  const input = fieldEl.querySelector('input');
  input.focus();
  input.select();
  let cancelled = false;
  const commit = async () => {
    if (cancelled) return;
    const val = input.value;
    if (val === current) { loadJobDetail(jobId, true); return; }
    await submitCorrection(jobId, field, val);
  };
  input.onblur = commit;
  input.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    if (e.key === 'Escape') { cancelled = true; loadJobDetail(jobId, true); }
  };
}

// ---------------------------------------------------------------------
// Cross-verification (reconcile)
// ---------------------------------------------------------------------
$('reconcileBtn')?.addEventListener('click', async () => {
  if (!confirm("This will compare all processed invoices to find mismatches. Continue?")) return;

  const btn = $('reconcileBtn');
  btn.textContent = "Verifying...";
  btn.disabled = true;

  try {
    const res = await apiFetch('/api/reconcile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ all: true })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Server returned ${res.status}`);

    let msg = `Verification Complete!\n\nInvoice numbers seen in 2+ documents: ${data.summary.matched_invoices}\nMismatches Found: ${data.summary.mismatches}\n\n`;
    if (data.summary.mismatches > 0) {
      msg += "Mismatch Details:\n";
      data.pairs.filter(p => !p.match).forEach(p => {
        const d = (p.diffs || []).map(x => x.field).join(', ');
        msg += `- Invoice ${p.invoice_number}: ${p.file_a} vs ${p.file_b} (differs: ${d})\n`;
      });
    } else {
      msg += "No mismatches found among related documents.";
    }
    alert(msg);
  } catch (e) {
    alert("Error during verification: " + e.message);
  } finally {
    btn.textContent = "🔄 Cross-Verify All Invoices";
    btn.disabled = false;
  }
});

// ---------------------------------------------------------------------
// Utils
// ---------------------------------------------------------------------
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function toast(msg, type = 'success') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity 0.3s'; }, 2800);
  setTimeout(() => el.remove(), 3200);
}

loadJobs();
