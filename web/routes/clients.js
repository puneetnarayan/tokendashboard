import { api, fmt, cacheGet, cacheSet, showToast } from '/web/app.js';

// Range presets in the Clients tab. "this month" and "last month" are
// calendar-month based (not rolling 30 days) so an invoice for "May"
// matches what the bookkeeper expects.
const PRESETS = [
  { key: '7d',         label: '7d',         fn: () => sinceDaysAgo(7) },
  { key: '30d',        label: '30d',        fn: () => sinceDaysAgo(30) },
  { key: '90d',        label: '90d',        fn: () => sinceDaysAgo(90) },
  { key: 'this-month', label: 'This month', fn: () => monthRange(0) },
  { key: 'last-month', label: 'Last month', fn: () => monthRange(-1) },
  { key: 'all',        label: 'All time',   fn: () => ({}) },
];

function sinceDaysAgo(d) {
  return { since: new Date(Date.now() - d * 86400_000).toISOString() };
}

function monthRange(offset) {
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth() + offset, 1);
  const end   = new Date(now.getFullYear(), now.getMonth() + offset + 1, 1);
  return { since: start.toISOString(), until: end.toISOString() };
}

function currentPreset() {
  const qs = new URLSearchParams((location.hash.split('?')[1] || ''));
  const key = qs.get('range') || '30d';
  return PRESETS.find(p => p.key === key) || PRESETS[1];
}

function setRange(key) {
  location.hash = '#/clients?range=' + encodeURIComponent(key);
}

export default async function (root) {
  const preset = currentPreset();
  const range = preset.fn();
  const url = '/api/clients-summary'
    + (range.since ? '?since=' + encodeURIComponent(range.since) : '')
    + (range.until ? (range.since ? '&' : '?') + 'until=' + encodeURIComponent(range.until) : '');

  let data = cacheGet(url);
  if (!data) {
    try { data = await api(url); cacheSet(url, data); }
    catch (e) {
      root.innerHTML = `<div class="card"><p class="muted">Failed to load clients: ${fmt.htmlSafe(String(e))}</p></div>`;
      return;
    }
  }

  render(root, data, preset, range);
}

function render(root, data, preset, range) {
  const tabs = PRESETS.map(p =>
    `<button data-preset="${p.key}" class="${p.key === preset.key ? 'active' : ''}">${p.label}</button>`
  ).join('');

  const totalHours = data.clients.reduce((a, c) => a + (c.active_seconds || 0), 0) / 3600;
  const totalAmount = data.clients.reduce((a, c) => a + (c.billable_amount || 0), 0);
  const billableCount = data.clients.filter(c => c.billable_amount != null).length;

  root.innerHTML = `
    <div class="flex" style="margin-bottom:14px">
      <h2 style="margin:0">Clients</h2>
      <span class="muted" style="font-size:13px">billable hours · ${rangeLabel(preset, range)}</span>
      <div class="spacer"></div>
      <div class="range-tabs" role="tablist">${tabs}</div>
      <a href="#/settings" class="pill" style="text-decoration:none;font-family:var(--sans)">Manage clients →</a>
    </div>

    <div class="row cols-4" style="margin-bottom:16px">
      <div class="card kpi"><div class="label">Total active</div><div class="value">${fmt.duration(totalHours * 3600)}</div></div>
      <div class="card kpi"><div class="label">Active clients</div><div class="value">${fmt.int(data.clients.filter(c => c.active_seconds > 0).length)}</div></div>
      <div class="card kpi"><div class="label">Unassigned hrs</div><div class="value">${fmt.duration(data.unassigned.reduce((a,p)=>a+(p.active_seconds||0),0))}</div></div>
      <div class="card kpi"><div class="label">Est. billable</div><div class="value">${totalAmount > 0 ? '$' + totalAmount.toLocaleString('en-US', {maximumFractionDigits: 0}) : '—'}</div><div class="sub">${billableCount} priced client${billableCount === 1 ? '' : 's'}</div></div>
    </div>

    ${data.clients.length === 0 ? emptyState() : clientTable(data.clients, range)}
    ${data.unassigned.length > 0 ? unassignedSection(data.unassigned) : ''}
    ${data.excluded.length > 0 ? excludedSection(data.excluded) : ''}

    <p class="muted" style="font-size:12px;margin-top:18px">
      Active time uses a 10-min idle threshold. Concurrent sessions in two terminals double-count.
      For invoicing, treat this as a defensible lower bound on Claude-driven hours;
      add manual top-ups for non-Claude work (Figma, browser testing, calls).
    </p>
  `;

  root.querySelectorAll('.range-tabs button').forEach(btn => {
    btn.addEventListener('click', () => setRange(btn.dataset.preset));
  });

  root.querySelectorAll('[data-action="override"]').forEach(btn => {
    btn.addEventListener('click', () => openOverridePicker(btn.dataset.project, data.clients));
  });
}

function clientTable(clients, range) {
  return `
    <div class="card">
      <table>
        <thead><tr>
          <th>client</th>
          <th>status</th>
          <th class="num">hours</th>
          <th class="num">rate</th>
          <th class="num">amount</th>
          <th class="num">eff. rate</th>
          <th>projects</th>
          <th></th>
        </tr></thead>
        <tbody>
          ${clients.map(c => clientRow(c, range)).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function clientRow(c, range) {
  const exportBase = `/api/clients/${c.id}/export`;
  const rangeQs = (range.since ? `&since=${encodeURIComponent(range.since)}` : '')
                + (range.until ? `&until=${encodeURIComponent(range.until)}` : '');
  const cur = c.currency || 'CAD';
  const rateCell = c.pricing_mode === 'fixed'
    ? `<span class="muted">fixed $${(c.fixed_price || 0).toLocaleString()}</span>`
    : c.hourly_rate != null
      ? `$${c.hourly_rate}/h`
      : `<span class="muted">—</span>`;
  const amountCell = c.billable_amount != null
    ? `$${c.billable_amount.toLocaleString('en-US', {maximumFractionDigits: 2})} ${cur}`
    : `<span class="muted">no rate</span>`;
  const effCell = c.effective_rate != null
    ? `$${c.effective_rate.toLocaleString('en-US', {maximumFractionDigits: 2})}/h`
    : '<span class="muted">—</span>';
  const projList = (c.projects || [])
    .map(p => `<span class="badge">${fmt.htmlSafe(p.project_name)}</span>`)
    .join(' ') || '<span class="muted">—</span>';

  return `
    <tr class="client-row" data-client-id="${c.id}">
      <td><b>${fmt.htmlSafe(c.name)}</b>${c.notes ? `<br><span class="muted" style="font-size:11px">${fmt.htmlSafe(c.notes)}</span>` : ''}</td>
      <td><span class="badge badge-${fmt.htmlSafe(c.status)}">${fmt.htmlSafe(c.status)}</span></td>
      <td class="num">${fmt.duration(c.active_seconds || 0)}</td>
      <td class="num">${rateCell}</td>
      <td class="num"><b>${amountCell}</b></td>
      <td class="num">${effCell}</td>
      <td>${projList}</td>
      <td class="num" style="white-space:nowrap">
        <a href="${exportBase}?fmt=csv${rangeQs}" class="pill" style="text-decoration:none;font-size:11px" download>CSV</a>
        <a href="${exportBase}?fmt=markdown${rangeQs}" class="pill" style="text-decoration:none;font-size:11px" download>MD</a>
      </td>
    </tr>
  `;
}

function unassignedSection(projects) {
  return `
    <div class="card" style="margin-top:16px">
      <h3>Unassigned projects <span class="muted" style="font-weight:400;font-size:13px">— no pattern matches</span></h3>
      <p class="muted" style="margin:-6px 0 12px;font-size:13px">
        These projects don't yet belong to a client. Add a pattern in <a href="#/settings">Settings</a> or tag them per-project below.
      </p>
      <table>
        <thead><tr><th>project</th><th class="num">hours</th><th class="num">sessions</th><th></th></tr></thead>
        <tbody>
          ${projects.slice(0, 30).map(p => `
            <tr>
              <td>${fmt.htmlSafe(p.project_name)}</td>
              <td class="num">${fmt.duration(p.active_seconds || 0)}</td>
              <td class="num">${fmt.int(p.sessions || 0)}</td>
              <td class="num"><button class="pill" data-action="override" data-project="${fmt.htmlSafe(p.project_name)}">tag…</button></td>
            </tr>`).join('')}
        </tbody>
      </table>
      ${projects.length > 30 ? `<p class="muted" style="font-size:12px;margin-top:8px">…and ${projects.length - 30} more</p>` : ''}
    </div>
  `;
}

function excludedSection(projects) {
  return `
    <div class="card" style="margin-top:16px">
      <h3>Excluded projects <span class="muted" style="font-weight:400;font-size:13px">— tagged "not billable"</span></h3>
      <ul style="margin:0;padding-left:18px">
        ${projects.map(p => `<li>${fmt.htmlSafe(p.project_name)} — ${fmt.duration(p.active_seconds || 0)}</li>`).join('')}
      </ul>
    </div>
  `;
}

function emptyState() {
  return `
    <div class="card" style="text-align:center;padding:48px">
      <h3>No clients yet</h3>
      <p class="muted" style="max-width:520px;margin:0 auto">
        Tag your projects to clients in <a href="#/settings">Settings</a> to start tracking billable hours.
        Each client owns a list of <em>patterns</em> — substrings of your project names that should map to them.
      </p>
      <p style="margin-top:16px"><a href="#/settings" class="pill" style="text-decoration:none">Open Settings →</a></p>
    </div>
  `;
}

function rangeLabel(preset, range) {
  if (preset.key === 'all') return 'all time';
  if (preset.key === 'this-month' || preset.key === 'last-month') {
    return new Date(range.since).toLocaleString('en', { month: 'long', year: 'numeric' });
  }
  return preset.label;
}

async function openOverridePicker(projectName, clients) {
  const options = [
    '<option value="">— pick a client —</option>',
    ...clients.map(c => `<option value="${c.id}">${fmt.htmlSafe(c.name)}</option>`),
    '<option value="null">Not billable (exclude)</option>',
  ].join('');
  const id = await prompt(
    `Tag "${projectName}" to which client?`,
    `<select id="ovr-pick" style="width:100%;padding:8px;background:var(--panel-2);border:1px solid var(--border);color:var(--text);border-radius:6px;font-size:14px">${options}</select>`,
    'ovr-pick'
  );
  if (id === undefined) return;
  const body = id === 'null'
    ? { project_name: projectName, client_id: null }
    : { project_name: projectName, client_id: parseInt(id, 10) };
  try {
    await api('/api/client-overrides', { method: 'POST', body });
    showToast(`Tagged ${projectName}`, 'ok');
    location.reload();
  } catch (e) {
    showToast('Tag failed: ' + e.message, 'err');
  }
}

// Tiny inline prompt helper. Bigger modal in settings.js will replace this
// eventually, but for one-click client picking this is fine.
function prompt(label, innerHtml, fieldId) {
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.className = 'modal-backdrop';
    wrap.innerHTML = `
      <div class="modal">
        <h3 style="margin:0 0 12px">${fmt.htmlSafe(label)}</h3>
        ${innerHtml}
        <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
          <button id="m-cancel" class="pill">Cancel</button>
          <button id="m-ok" class="pill" style="background:var(--accent);color:#0A0E14;border:0">OK</button>
        </div>
      </div>`;
    document.body.appendChild(wrap);
    const cleanup = (val) => { wrap.remove(); resolve(val); };
    wrap.querySelector('#m-cancel').addEventListener('click', () => cleanup(undefined));
    wrap.querySelector('#m-ok').addEventListener('click', () => {
      const el = wrap.querySelector('#' + fieldId);
      cleanup(el?.value ?? '');
    });
    wrap.addEventListener('click', e => { if (e.target === wrap) cleanup(undefined); });
  });
}
