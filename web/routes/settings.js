import { api, state, $ } from '/web/app.js';

function customModelRows(cm) {
  const entries = Object.entries(cm);
  if (!entries.length) return '<tr><td colspan="4" class="muted" style="padding:14px 12px">No custom models yet.</td></tr>';
  return entries.map(([k, v]) => `
    <tr data-model="${k.replace(/"/g, '&quot;')}">
      <td><span class="badge">${k}</span></td>
      <td class="num">$${(+v.input).toFixed(2)}</td>
      <td class="num">$${(+v.output).toFixed(2)}</td>
      <td><button class="ghost rm-custom" style="padding:2px 8px;font-size:11px">remove</button></td>
    </tr>`).join('');
}

export default async function (root) {
  const cur = await api('/api/plan');
  const plans = Object.entries(cur.pricing.plans);
  const customModels = cur.custom_models || {};

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
      <p class="muted" style="margin-top:8px;font-size:11px">Rates per 1M tokens, USD.</p>

      <hr class="divider">

      <h3>Custom model pricing</h3>
      <p class="muted" style="margin:0 0 12px">Add input/output rates for models not in the table above (e.g. third-party Bedrock models like <code>zai.glm-5</code>). Cache rates default to $0.</p>
      <table>
        <thead><tr><th>model</th><th class="num">input $/1M</th><th class="num">output $/1M</th><th></th></tr></thead>
        <tbody id="custom-tbody">${customModelRows(customModels)}</tbody>
      </table>
      <div class="flex" style="margin-top:12px;gap:8px">
        <input id="cm-id"  placeholder="model ID (e.g. zai.glm-5)" style="flex:1;background:var(--panel-2);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text);font-family:var(--mono);font-size:12px">
        <input id="cm-in"  placeholder="input $/1M"  type="number" min="0" step="0.01" style="width:130px;background:var(--panel-2);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text)">
        <input id="cm-out" placeholder="output $/1M" type="number" min="0" step="0.01" style="width:130px;background:var(--panel-2);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text)">
        <button class="primary" id="cm-add">Add</button>
        <span id="cm-msg" class="muted"></span>
      </div>

      <hr class="divider">

      <h3>Privacy</h3>
      <p class="muted">Press <code>Cmd/Ctrl + B</code> anywhere to blur prompt text and other sensitive content for screenshots.</p>
    </div>`;

  $('#save').addEventListener('click', async () => {
    const plan = $('#plan').value;
    await fetch('/api/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan }) });
    state.plan = plan;
    document.getElementById('plan-pill').textContent = plan;
    $('#msg').textContent = 'Saved.';
    $('#msg').style.color = 'var(--good)';
  });

  async function postCustomModel(payload) {
    const res = await fetch('/api/custom-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('custom-tbody').innerHTML = customModelRows(data.custom_models || {});
      wireRemoveButtons();
    }
    return data;
  }

  function wireRemoveButtons() {
    document.querySelectorAll('.rm-custom').forEach(btn => {
      btn.addEventListener('click', () => {
        const model = btn.closest('tr').dataset.model;
        postCustomModel({ action: 'delete', model });
      });
    });
  }

  wireRemoveButtons();

  $('#cm-add').addEventListener('click', async () => {
    const model  = $('#cm-id').value.trim();
    const input  = parseFloat($('#cm-in').value)  || 0;
    const output = parseFloat($('#cm-out').value) || 0;
    const msg    = $('#cm-msg');
    if (!model) { msg.textContent = 'Model ID required.'; msg.style.color = 'var(--bad)'; return; }
    await postCustomModel({ action: 'set', model, input, output });
    $('#cm-id').value = ''; $('#cm-in').value = ''; $('#cm-out').value = '';
    msg.textContent = 'Saved.'; msg.style.color = 'var(--good)';
    setTimeout(() => { msg.textContent = ''; }, 2000);
  });
}
