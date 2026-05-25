import { api, state, fmt, showToast, $ } from '/web/app.js';

export default async function (root) {
  const [cur, clients] = await Promise.all([
    api('/api/plan'),
    api('/api/clients').catch(() => []),
  ]);
  const plans = Object.entries(cur.pricing.plans);
  root.innerHTML = `
    <div class="card">
      <h2>Settings</h2>
      <h3 style="margin-top:16px">Plan</h3>
      <p class="muted" style="margin:0 0 12px">Sets how cost is displayed. API mode shows pay-per-token rates. Subscription modes show what you actually pay each month.</p>
      <div class="flex">
        <select id="plan">
          ${plans.map(([k,v]) => `<option value="${k}" ${k===cur.plan?'selected':''}>${v.label}${v.monthly?` — $${v.monthly}/mo`:''}</option>`).join('')}
        </select>
        <button class="primary" id="save">Save</button>
        <span id="msg" class="muted"></span>
      </div>

      <hr class="divider">

      <h3>Pricing table</h3>
      <p class="muted" style="margin:0 0 12px">Edit <code>pricing.json</code> in the project root to change rates. Reload the page after editing.</p>
      <table>
        <thead><tr><th>model</th><th class="num">input</th><th class="num">output</th><th class="num">cache read</th><th class="num">cache 5m</th><th class="num">cache 1h</th></tr></thead>
        <tbody>
          ${Object.entries(cur.pricing.models).map(([k,v]) => `
            <tr><td><span class="badge ${v.tier}">${k}</span></td>
              <td class="num">$${v.input.toFixed(2)}</td>
              <td class="num">$${v.output.toFixed(2)}</td>
              <td class="num">$${v.cache_read.toFixed(2)}</td>
              <td class="num">$${v.cache_create_5m.toFixed(2)}</td>
              <td class="num">$${v.cache_create_1h.toFixed(2)}</td>
            </tr>`).join('')}
        </tbody>
      </table>
      <p class="muted" style="margin-top:8px;font-size:12px">Rates per 1M tokens, USD.</p>

      <hr class="divider">

      <h3>Privacy</h3>
      <p class="muted">Press <code>Cmd/Ctrl + B</code> anywhere to blur prompt text and other sensitive content for screenshots.</p>
    </div>

    <div class="card client-list-card" style="margin-top:16px">
      <div class="flex">
        <h2 style="margin:0">Clients</h2>
        <span class="muted" style="font-size:13px">Tag projects to clients to enable billable-hours reporting</span>
        <div class="spacer"></div>
        <button class="pill" id="add-client" style="background:var(--accent);color:#0A0E14;border:0">+ Add client</button>
      </div>
      <div id="client-list">${renderClientList(clients)}</div>
    </div>
  `;

  $('#save').addEventListener('click', async () => {
    const plan = $('#plan').value;
    await api('/api/plan', { method: 'POST', body: { plan } });
    state.plan = plan;
    document.getElementById('plan-pill').textContent = plan;
    $('#msg').textContent = 'Saved.';
    $('#msg').style.color = 'var(--good)';
  });

  $('#add-client').addEventListener('click', () => openClientForm(null, refresh));
  root.querySelectorAll('[data-client-edit]').forEach(b =>
    b.addEventListener('click', () => {
      const c = clients.find(c => String(c.id) === b.dataset.clientEdit);
      openClientForm(c, refresh);
    })
  );
  root.querySelectorAll('[data-client-delete]').forEach(b =>
    b.addEventListener('click', async () => {
      const c = clients.find(c => String(c.id) === b.dataset.clientDelete);
      if (!confirm(`Delete client "${c.name}"? This removes the client and its patterns. Sessions stay in the DB; they'll just stop being tagged.`)) return;
      await api('/api/clients/delete', { method: 'POST', body: { id: c.id } });
      showToast(`Deleted ${c.name}`, 'ok');
      refresh();
    })
  );

  async function refresh() {
    const fresh = await api('/api/clients');
    $('#client-list').innerHTML = renderClientList(fresh);
    // Re-bind the row buttons (the list HTML was just rewritten).
    $('#client-list').querySelectorAll('[data-client-edit]').forEach(b =>
      b.addEventListener('click', () => {
        const c = fresh.find(c => String(c.id) === b.dataset.clientEdit);
        openClientForm(c, refresh);
      })
    );
    $('#client-list').querySelectorAll('[data-client-delete]').forEach(b =>
      b.addEventListener('click', async () => {
        const c = fresh.find(c => String(c.id) === b.dataset.clientDelete);
        if (!confirm(`Delete client "${c.name}"?`)) return;
        await api('/api/clients/delete', { method: 'POST', body: { id: c.id } });
        showToast(`Deleted ${c.name}`, 'ok');
        refresh();
      })
    );
  }
}

function renderClientList(clients) {
  if (!clients.length) {
    return `<p class="muted" style="margin-top:12px">No clients yet. Click <b>+ Add client</b> to create your first one.</p>`;
  }
  return clients.map(c => `
    <div class="client-card">
      <div class="flex">
        <div>
          <b style="font-size:15px">${fmt.htmlSafe(c.name)}</b>
          <span class="badge badge-${fmt.htmlSafe(c.status)}" style="margin-left:8px">${fmt.htmlSafe(c.status)}</span>
        </div>
        <div class="spacer"></div>
        <button class="pill" data-client-edit="${c.id}">Edit</button>
        <button class="pill" data-client-delete="${c.id}" style="color:#e08080">Delete</button>
      </div>
      <div class="meta">
        ${pricingSummary(c)}
        <span>·</span>
        <span>${c.patterns.length} pattern${c.patterns.length === 1 ? '' : 's'}:</span>
        ${(c.patterns || []).map(p => `<span class="pattern-pill">${fmt.htmlSafe(p)}</span>`).join('') || '<span class="muted">none yet</span>'}
      </div>
      ${c.notes ? `<p class="muted" style="margin:8px 0 0;font-size:12px">${fmt.htmlSafe(c.notes)}</p>` : ''}
    </div>
  `).join('');
}

function pricingSummary(c) {
  const cur = c.currency || 'CAD';
  if (c.fixed_price != null) return `<span>Fixed: $${c.fixed_price} ${cur}</span>`;
  if (c.hourly_rate != null) return `<span>$${c.hourly_rate} ${cur}/h</span>`;
  return `<span class="muted">no rate set</span>`;
}

function openClientForm(existing, onSaved) {
  const c = existing || { name: '', hourly_rate: '', fixed_price: '', currency: 'CAD', status: 'active', notes: '', patterns: [] };
  const isEdit = !!existing;
  const wrap = document.createElement('div');
  wrap.className = 'modal-backdrop';
  wrap.innerHTML = `
    <div class="modal" style="min-width:480px">
      <h3 style="margin:0 0 12px">${isEdit ? 'Edit' : 'Add'} client</h3>
      <label>Name</label>
      <input type="text" id="f-name" value="${fmt.htmlSafe(c.name)}" placeholder="e.g. Luisa Buchaibe">

      <div class="row cols-2" style="gap:12px;margin-top:0">
        <div>
          <label>Hourly rate</label>
          <input type="number" step="0.01" id="f-hourly" value="${c.hourly_rate ?? ''}" placeholder="120">
        </div>
        <div>
          <label>Fixed price (overrides hourly)</label>
          <input type="number" step="0.01" id="f-fixed" value="${c.fixed_price ?? ''}" placeholder="3500">
        </div>
      </div>

      <div class="row cols-2" style="gap:12px;margin-top:0">
        <div>
          <label>Currency</label>
          <select id="f-currency">
            <option ${c.currency==='CAD'?'selected':''}>CAD</option>
            <option ${c.currency==='USD'?'selected':''}>USD</option>
            <option ${c.currency==='EUR'?'selected':''}>EUR</option>
            <option ${c.currency==='GBP'?'selected':''}>GBP</option>
          </select>
        </div>
        <div>
          <label>Status</label>
          <select id="f-status">
            <option value="active"   ${c.status==='active'?'selected':''}>active</option>
            <option value="paused"   ${c.status==='paused'?'selected':''}>paused</option>
            <option value="archived" ${c.status==='archived'?'selected':''}>archived</option>
          </select>
        </div>
      </div>

      <label>Patterns <span class="muted" style="text-transform:none;letter-spacing:0;font-size:11px">(one per line — case-insensitive substring match against rolled-up project_name)</span></label>
      <textarea id="f-patterns" placeholder="luisa&#10;luisabuchaibe">${fmt.htmlSafe((c.patterns || []).join('\n'))}</textarea>

      <label>Notes (optional)</label>
      <input type="text" id="f-notes" value="${fmt.htmlSafe(c.notes || '')}" placeholder="any context worth remembering">

      <div style="margin-top:16px;display:flex;gap:8px;justify-content:flex-end">
        <button id="m-cancel" class="pill">Cancel</button>
        <button id="m-ok" class="pill" style="background:var(--accent);color:#0A0E14;border:0">${isEdit ? 'Save' : 'Create'}</button>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  const close = () => wrap.remove();
  wrap.querySelector('#m-cancel').addEventListener('click', close);
  wrap.addEventListener('click', e => { if (e.target === wrap) close(); });
  wrap.querySelector('#m-ok').addEventListener('click', async () => {
    const payload = {
      id: existing?.id,
      name: wrap.querySelector('#f-name').value.trim(),
      hourly_rate: parseFloatOrNull(wrap.querySelector('#f-hourly').value),
      fixed_price: parseFloatOrNull(wrap.querySelector('#f-fixed').value),
      currency: wrap.querySelector('#f-currency').value,
      status: wrap.querySelector('#f-status').value,
      notes: wrap.querySelector('#f-notes').value.trim(),
      patterns: wrap.querySelector('#f-patterns').value
        .split('\n').map(s => s.trim()).filter(Boolean),
    };
    if (!payload.name) { showToast('Name is required', 'err'); return; }
    try {
      await api('/api/clients', { method: 'POST', body: payload });
      showToast(`${isEdit ? 'Updated' : 'Created'} ${payload.name}`, 'ok');
      close();
      onSaved?.();
    } catch (e) {
      showToast('Save failed: ' + e.message, 'err');
    }
  });
}

function parseFloatOrNull(s) {
  const v = parseFloat((s || '').toString().trim());
  return Number.isFinite(v) ? v : null;
}
