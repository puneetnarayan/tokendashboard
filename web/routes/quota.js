import { api, fmt } from '/web/app.js';

const PLANS = ['pro', 'max', 'max-20x', 'api'];

function progressBar(current, limit, alertPct) {
  const pct = limit > 0 ? Math.min(100, (current / limit) * 100) : 0;
  const over = pct >= alertPct;
  const critical = pct >= alertPct + 15;
  const cls = critical ? 'critical' : over ? 'warning' : 'ok';
  return `
    <div class="progress-row">
      <div class="progress-bar ${cls}">
        <div class="progress-fill" style="width:${pct.toFixed(1)}%"></div>
      </div>
      <div class="progress-label">
        ${fmt.int(current)} / ${fmt.int(limit)} (${pct.toFixed(0)}%)
      </div>
    </div>
  `;
}

function settingsCard(cfg) {
  const planOptions = PLANS.map(p =>
    `<option value="${p}" ${p === cfg.plan ? 'selected' : ''}>${p}</option>`
  ).join('');
  return `
    <form id="quota-settings" class="settings-card">
      <h3>Settings</h3>
      <label>Plan <select name="plan">${planOptions}</select></label>
      <label>5h quota (tokens) <input name="quota_5h" type="number" value="${cfg.quota_5h}" min="1000" max="100000000"></label>
      <label>Weekly quota (tokens) <input name="quota_weekly" type="number" value="${cfg.quota_weekly}" min="1000" max="100000000"></label>
      <label>Alert at 5h (%) <input name="alert_pct_5h" type="number" value="${cfg.alert_pct_5h}" min="1" max="99"></label>
      <label>Alert at weekly (%) <input name="alert_pct_weekly" type="number" value="${cfg.alert_pct_weekly}" min="1" max="99"></label>
      <label>Alert per session (%) <input name="alert_pct_session" type="number" value="${cfg.alert_pct_session}" min="1" max="99"></label>
      <button type="submit">Save</button>
      <span id="quota-save-status"></span>
    </form>
  `;
}

export default async function (root) {
  root.innerHTML = '<div class="loading">Loading quota…</div>';
  const data = await api('/api/quota');
  const { config, usage_5h, usage_weekly } = data;

  root.innerHTML = `
    <h2>Quota</h2>
    <section class="quota-window">
      <h3>5-hour rolling window</h3>
      ${progressBar(usage_5h.total_billable, config.quota_5h, config.alert_pct_5h)}
    </section>
    <section class="quota-window">
      <h3>7-day rolling window</h3>
      ${progressBar(usage_weekly.total_billable, config.quota_weekly, config.alert_pct_weekly)}
    </section>
    <section>${settingsCard(config)}</section>
  `;

  const form = root.querySelector('#quota-settings');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const payload = {};
    for (const [k, v] of fd.entries()) {
      payload[k] = k === 'plan' ? v : parseInt(v, 10);
    }
    const status = root.querySelector('#quota-save-status');
    status.textContent = 'Saving…';
    try {
      const r = await fetch('/api/quota/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const err = await r.json();
        status.textContent = 'Error: ' + (err.error || r.status);
        return;
      }
      status.textContent = 'Saved.';
      setTimeout(() => { location.reload(); }, 400);
    } catch (err) {
      status.textContent = 'Error: ' + err.message;
    }
  });
}
