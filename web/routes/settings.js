import { api, state, $, t } from '/web/app.js';

export default async function (root) {
  const cur = await api('/api/plan');
  const plans = Object.entries(cur.pricing.plans);
  root.innerHTML = `
    <div class="card">
      <h2>${t('st_title')}</h2>
      <h3 style="margin-top:16px">${t('st_plan')}</h3>
      <p class="muted" style="margin:0 0 12px">${t('st_plan_sub')}</p>
      <div class="flex">
        <select id="plan">
          ${plans.map(([k,v]) => `<option value="${k}" ${k===cur.plan?'selected':''}>${v.label}${v.monthly?` — $${v.monthly}/mo`:''}</option>`).join('')}
        </select>
        <button class="primary" id="save">${t('st_save')}</button>
        <span id="msg" class="muted"></span>
      </div>

      <hr class="divider">

      <h3>${t('st_pricing')}</h3>
      <p class="muted" style="margin:0 0 12px">${t('st_pricing_sub')}</p>
      <table>
        <thead><tr><th>${t('st_col_model')}</th><th class="num">${t('st_col_in')}</th><th class="num">${t('st_col_out')}</th><th class="num">${t('st_col_cr')}</th><th class="num">${t('st_col_c5m')}</th><th class="num">${t('st_col_c1h')}</th></tr></thead>
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
      <p class="muted" style="margin-top:8px;font-size:11px">${t('st_rates')}</p>

      <hr class="divider">

      <h3>${t('st_privacy')}</h3>
      <p class="muted">${t('st_privacy_body')}</p>
    </div>`;

  $('#save').addEventListener('click', async () => {
    const plan = $('#plan').value;
    await fetch('/api/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan }) });
    state.plan = plan;
    document.getElementById('plan-pill').textContent = plan;
    $('#msg').textContent = t('st_saved');
    $('#msg').style.color = 'var(--good)';
  });
}
