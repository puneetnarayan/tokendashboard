import { api, fmt } from '/web/app.js';
import { mixedBarLineChart, stackedBarChart, donutChart, barChart } from '/web/charts.js';

const RANGES = [
  { key: '7d',  label: '7d',  days: 7 },
  { key: '30d', label: '30d', days: 30 },
  { key: '90d', label: '90d', days: 90 },
  { key: 'all', label: 'All', days: null },
];

function readRange() {
  const q = (location.hash.split('?')[1] || '');
  const m = /(?:^|&)range=([^&]+)/.exec(q);
  const k = m && decodeURIComponent(m[1]);
  return RANGES.find(r => r.key === k) || RANGES[1];
}

function writeRange(key) {
  location.hash = '#/litellm?range=' + encodeURIComponent(key);
}

const rangeTabs = (active) => `
  <div class="range-tabs" role="tablist">
    ${RANGES.map(r => `<button data-range="${r.key}" class="${r.key === active ? 'active' : ''}">${r.label}</button>`).join('')}
  </div>`;

function wireRangeTabs(root) {
  root.querySelectorAll('.range-tabs button').forEach(btn => {
    btn.addEventListener('click', () => writeRange(btn.dataset.range));
  });
}

// ── state panels ──────────────────────────────────────────────────────────

function renderNotConfigured(root) {
  root.innerHTML = `
    <div class="card" style="max-width:600px;margin:40px auto;text-align:center">
      <h2 style="margin-bottom:12px">No LiteLLM proxy detected</h2>
      <p style="color:var(--muted);line-height:1.7;margin-bottom:16px">
        If your Claude Code goes through the org's LiteLLM proxy, set
        <code>ANTHROPIC_BASE_URL</code> and <code>ANTHROPIC_AUTH_TOKEN</code>
        in the <code>env</code> block of <code>~/.claude/settings.json</code>
        and your actual spend will appear here automatically.
        You can also configure it manually in
        <a href="#/settings">Settings</a>.
      </p>
      <p class="muted" style="font-size:12px">
        If you use Bedrock or SSO directly, this tab is not applicable — your
        local estimates in Overview are your primary cost view.
      </p>
    </div>`;
}

function renderDisabled(root) {
  root.innerHTML = `
    <div class="card" style="max-width:500px;margin:40px auto;text-align:center">
      <h2 style="margin-bottom:12px">LiteLLM integration is disabled</h2>
      <p class="muted">Re-enable it in <a href="#/settings">Settings → LiteLLM proxy</a>.</p>
    </div>`;
}

function renderInvalidKey(root, status) {
  root.innerHTML = `
    <div class="card" style="max-width:500px;margin:40px auto;text-align:center">
      <h2 style="margin-bottom:12px">LiteLLM key rejected (401)</h2>
      <p class="muted" style="margin-bottom:16px">
        Proxy: <strong>${fmt.htmlSafe(status.proxy_origin || '—')}</strong><br>
        If your key was rotated, update <code>ANTHROPIC_AUTH_TOKEN</code>
        in <code>~/.claude/settings.json</code>.
      </p>
      <button class="primary" id="ll-test-btn">Test connection</button>
      <span id="ll-test-res" class="muted" style="font-size:12px;margin-left:10px"></span>
    </div>`;
  document.getElementById('ll-test-btn').addEventListener('click', async () => {
    const res = document.getElementById('ll-test-res');
    res.textContent = 'Testing…';
    const r = await fetch('/api/litellm/test', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(x=>x.json());
    res.textContent = r.state === 'active' ? '✓ Connected — reload the page' : `✗ Still ${r.state}`;
  });
}

function renderUnreachable(root, status, hasCache) {
  const staleMsg = `<div class="banner warn" style="margin-bottom:16px;padding:8px 12px;background:var(--panel-2);border:1px solid var(--warn);border-radius:6px;font-size:12px">
    Proxy unreachable — are you on VPN? Showing cached data (last synced ${status.last_sync ? status.last_sync.slice(0,16).replace('T',' ') : 'unknown'}).
    Will resume automatically when the proxy is reachable.
  </div>`;
  if (!hasCache) {
    root.innerHTML = `
      <div class="card" style="max-width:500px;margin:40px auto;text-align:center">
        <h2 style="margin-bottom:12px">Can't reach ${fmt.htmlSafe(status.proxy_origin || 'proxy')}</h2>
        <p class="muted" style="margin-bottom:16px">
          The LiteLLM proxy requires a VPN connection — connect to VPN and this
          page will populate automatically within a few minutes.
        </p>
        <button class="primary" id="ll-retry-btn">Retry now</button>
        <span id="ll-retry-res" class="muted" style="font-size:12px;margin-left:10px"></span>
      </div>`;
    document.getElementById('ll-retry-btn').addEventListener('click', async () => {
      const res = document.getElementById('ll-retry-res');
      res.textContent = 'Syncing…';
      const r = await fetch('/api/litellm/sync', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(x=>x.json());
      res.textContent = r.state === 'active' ? '✓ Connected — reload' : `Still ${r.state}`;
    });
    return false; // no cache to render
  }
  // has cache — inject stale banner before the rest of the render
  root.insertAdjacentHTML('afterbegin', staleMsg);
  return true; // render the tab with cached data
}

// ── active state render ───────────────────────────────────────────────────

export default async function (root) {
  root.innerHTML = '<p style="padding:40px;color:var(--muted)">Loading LiteLLM…</p>';

  let status;
  try {
    status = await api('/api/litellm/status');
  } catch (e) {
    root.innerHTML = `<div class="card"><p class="muted">Failed to load status: ${fmt.htmlSafe(String(e))}</p></div>`;
    return;
  }

  if (status.state === 'not_configured') return renderNotConfigured(root);
  if (status.state === 'disabled')       return renderDisabled(root);
  if (status.state === 'invalid_key')    return renderInvalidKey(root, status);

  const range = readRange();

  if (status.state === 'unreachable') {
    // check if we have any cached data
    let hasCacheData = false;
    try {
      const check = await api(`/api/litellm/daily?range=${range.key}`);
      hasCacheData = (check.days || []).some(d => d.spend > 0 || d.total_tokens > 0);
    } catch (_) {}
    root.innerHTML = '';
    const proceed = renderUnreachable(root, status, hasCacheData);
    if (!proceed) return;
  } else {
    root.innerHTML = '';
  }

  // Header strip
  const srcBadge = status.source ? `<span class="badge" style="font-size:10px;margin-left:6px">${status.source}</span>` : '';
  const syncedAgo = status.last_sync ? _relTime(status.last_sync) : 'never';
  const staleHtml = status.stale && status.state !== 'unreachable'
    ? `<span class="badge warn" style="margin-left:8px;font-size:10px">stale data</span>` : '';

  const headerHtml = `
    <div class="card" style="margin-bottom:16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <span style="font-weight:600;font-size:13px">${fmt.htmlSafe(status.proxy_origin || '')}</span>
      ${srcBadge}
      ${status.user_alias ? `<span class="muted" style="font-size:12px">${fmt.htmlSafe(status.user_alias)}</span>` : ''}
      ${status.key_masked ? `<code style="font-size:11px">${fmt.htmlSafe(status.key_masked)}</code>` : ''}
      <span class="spacer"></span>
      <span class="muted" style="font-size:12px">Last synced: ${syncedAgo}${staleHtml}</span>
      <button class="ghost" id="ll-sync-now" style="font-size:12px">Sync now</button>
    </div>`;

  const explainerHtml = `
    <details class="card glossary" style="margin-bottom:16px">
      <summary><h3 style="display:inline-block;margin:0;font-size:13px">What do these numbers mean?</h3><span class="muted" style="font-size:12px"> — click to expand</span></summary>
      <p style="margin:8px 0 0;font-size:12px;line-height:1.7;color:var(--muted)">
        LiteLLM is the org's LLM proxy. Numbers here are what the org is actually
        billed for your virtual key — across <em>all tools that use it</em>, not just Claude Code.
        The estimate comes from your local Claude Code transcripts.
        A gap between them usually means other tools share your key.
      </p>
    </details>`;

  const rangeHtml = `
    <div class="flex" style="margin-bottom:16px">
      <h2 style="margin:0;font-size:16px">LiteLLM</h2>
      <div class="spacer"></div>
      ${rangeTabs(range.key)}
    </div>`;

  root.insertAdjacentHTML('beforeend', headerHtml + explainerHtml + rangeHtml);

  // Fetch data
  let summary, daily, models;
  try {
    [summary, daily, models] = await Promise.all([
      api(`/api/litellm/summary?range=${range.key}`),
      api(`/api/litellm/daily?range=${range.key}`),
      api(`/api/litellm/models?range=${range.key}`),
    ]);
  } catch (e) {
    root.insertAdjacentHTML('beforeend', `<div class="card"><p class="muted">Failed to load data: ${fmt.htmlSafe(String(e))}</p></div>`);
    return;
  }

  // KPI cards
  const delta = summary.delta ?? 0;
  const deltaSpend = summary.spend || 0;
  const isNeutral = deltaSpend === 0 || Math.abs(delta) / deltaSpend < 0.1;
  const deltaColor = isNeutral ? 'var(--muted)' : 'var(--warn)';
  const deltaSign = delta >= 0 ? '+' : '';

  const budgetHtml = (() => {
    if (!summary.budget) return '';
    const b = summary.budget;
    const pct = b.max > 0 ? b.spent / b.max : 0;
    const barColor = pct >= 0.9 ? 'var(--bad)' : pct >= 0.75 ? 'var(--warn)' : 'var(--good)';
    return `
      <div class="card kpi" style="grid-column:span 2">
        <div class="label">Budget (${fmt.htmlSafe(b.duration || '')})</div>
        <div class="value">${fmt.usd(b.spent)} <span class="muted" style="font-size:12px">of ${fmt.usd(b.max)}</span></div>
        <div style="margin-top:6px;height:4px;background:var(--panel-2);border-radius:2px">
          <div style="width:${Math.min(100,pct*100).toFixed(1)}%;height:100%;background:${barColor};border-radius:2px;transition:width .3s"></div>
        </div>
      </div>`;
  })();

  const kpiHtml = `
    <div class="row" style="margin-bottom:16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px">
      <div class="card kpi cost">
        <div class="label">Actual spend</div>
        <div class="value">${fmt.usd(summary.spend)}</div>
      </div>
      <div class="card kpi">
        <div class="label">Local estimate</div>
        <div class="value">${fmt.usd(summary.local_estimate_spend)}</div>
      </div>
      <div class="card kpi">
        <div class="label">Delta</div>
        <div class="value" style="color:${deltaColor}">${deltaSign}${fmt.usd(delta)}</div>
        <div class="sub" style="font-size:10px;color:var(--muted)">actual − estimate</div>
      </div>
      <div class="card kpi">
        <div class="label">API requests</div>
        <div class="value">${fmt.int(summary.api_requests)}</div>
      </div>
      <div class="card kpi">
        <div class="label">Total tokens</div>
        <div class="value">${fmt.compact(summary.total_tokens)}</div>
        <div class="sub">${fmt.compact(summary.prompt_tokens)} prompt / ${fmt.compact(summary.completion_tokens)} completion</div>
      </div>
      ${summary.avg_cost_per_request != null ? `
      <div class="card kpi">
        <div class="label">Avg cost/req</div>
        <div class="value">${fmt.usd4(summary.avg_cost_per_request)}</div>
      </div>` : ''}
      ${budgetHtml}
    </div>`;

  root.insertAdjacentHTML('beforeend', kpiHtml);

  // Charts
  const days = daily.days || [];
  const dates = days.map(d => d.date);
  const actualSpend = days.map(d => d.spend);
  const estSpend    = days.map(d => d.local_estimate_spend);

  const chartsHtml = `
    <div class="card" style="margin-bottom:16px">
      <h3>Daily spend — actual vs estimate</h3>
      <p class="muted" style="margin:-4px 0 10px;font-size:12px">
        Bars = what the org is billed (LiteLLM). Dashed line = local Claude Code estimate.
        A gap means spend from other tools on the same key.
      </p>
      <div id="ch-ll-daily" style="height:260px"></div>
    </div>

    <div class="row cols-2" style="margin-bottom:16px">
      <div class="card">
        <h3>Token share by model</h3>
        <div id="ch-ll-donut" style="height:260px"></div>
      </div>
      <div class="card">
        <h3>Prompt vs completion tokens daily</h3>
        <div id="ch-ll-tok" style="height:260px"></div>
      </div>
    </div>`;

  root.insertAdjacentHTML('beforeend', chartsHtml);

  // Chart 1 — daily actual vs estimate
  mixedBarLineChart(document.getElementById('ch-ll-daily'), {
    categories:  dates,
    bar:         actualSpend,
    line:        estSpend,
    barLabel:    'actual ($)',
    lineLabel:   'estimate ($)',
    formatter:   v => '$' + Number(v).toFixed(4),
  });

  // Chart 2 — token share by model (donut)
  const modelData = (models.models || []).slice(0, 8).map(m => ({
    name:  m.model,
    value: m.total_tokens,
  })).filter(d => d.value > 0);
  donutChart(document.getElementById('ch-ll-donut'), modelData);

  // Chart 3 — prompt vs completion daily
  stackedBarChart(document.getElementById('ch-ll-tok'), {
    categories: dates,
    series: [
      { name: 'prompt',     values: days.map(d => d.prompt_tokens     ?? 0), color: '#4A9EFF' },
      { name: 'completion', values: days.map(d => d.completion_tokens ?? 0), color: '#7C5CFF' },
    ],
    formatter: v => Number(v).toLocaleString() + ' tok',
  });

  // Models table
  const allModels = models.models || [];
  const tableHtml = `
    <div class="card" style="margin-bottom:16px">
      <h3>Spend by model</h3>
      <table>
        <thead>
          <tr>
            <th>Model</th>
            <th class="num">Spend</th>
            <th class="num">% spend</th>
            <th class="num">API reqs</th>
            <th class="num">Avg cost/req</th>
            <th class="num">Prompt tok</th>
            <th class="num">Completion tok</th>
            <th class="num">Total tok</th>
          </tr>
        </thead>
        <tbody>
          ${allModels.length === 0
            ? '<tr><td colspan="8" class="muted">No data in this range</td></tr>'
            : allModels.map(m => `
              <tr>
                <td><span class="badge">${fmt.htmlSafe(m.model)}</span></td>
                <td class="num">${fmt.usd4(m.spend)}</td>
                <td class="num">${(m.share_pct ?? 0).toFixed(1)}%</td>
                <td class="num">${fmt.int(m.api_requests)}</td>
                <td class="num">${m.avg_cost_per_request != null ? fmt.usd4(m.avg_cost_per_request) : '—'}</td>
                <td class="num">${fmt.compact(m.prompt_tokens)}</td>
                <td class="num">${fmt.compact(m.completion_tokens)}</td>
                <td class="num">${fmt.compact(m.total_tokens)}</td>
              </tr>`).join('')}
        </tbody>
      </table>
    </div>`;

  root.insertAdjacentHTML('beforeend', tableHtml);

  // Delta explainer
  root.insertAdjacentHTML('beforeend', `
    <p class="muted" style="font-size:11px;text-align:center;margin-bottom:24px">
      Delta = usage outside Claude Code, or pricing differences.
    </p>`);

  // Monthly comparison section
  try {
    const monthly = await api('/api/litellm/monthly');
    renderMonthly(root, monthly);
  } catch (e) {
    root.insertAdjacentHTML('beforeend', `<p class="muted" style="font-size:11px;margin-bottom:16px">Monthly view unavailable: ${fmt.htmlSafe(String(e))}</p>`);
  }

  wireRangeTabs(root);

  document.getElementById('ll-sync-now').addEventListener('click', async (e) => {
    e.target.textContent = 'Syncing…';
    e.target.disabled = true;
    await fetch('/api/litellm/sync', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    e.target.textContent = 'Sync now';
    e.target.disabled = false;
  });
}

// ── monthly view ──────────────────────────────────────────────────────────

function renderMonthly(root, monthly) {
  const months     = monthly.months      || [];
  const compare    = monthly.compare     || {};
  const deltas     = monthly.model_deltas || [];
  const keyDeltas  = monthly.key_deltas   || [];

  // Need ≥2 months with any data for compare card and delta table
  const hasData = months.filter(m => m.spend > 0).length >= 2;

  root.insertAdjacentHTML('beforeend', `
    <hr class="divider">
    <div class="flex" style="margin-bottom:16px">
      <h2 style="margin:0;font-size:16px">Month-over-month</h2>
    </div>`);

  // Compare card (only if ≥2 months of data)
  if (hasData && compare.current_mtd != null) {
    const cp      = compare.change_pct;
    const neutral = cp == null || Math.abs(cp) < 10;
    const color   = neutral ? 'var(--muted)' : 'var(--warn)';
    const sign    = (cp != null && cp >= 0) ? '+' : '';
    const cpStr   = cp != null ? `${sign}${cp.toFixed(1)}%` : '—';
    const curLabel  = compare.current_mtd_month   || '';
    const prevLabel = compare.previous_month       || '';
    const dayLabel  = `${prevLabel} day 1–${compare.same_day}`;

    root.insertAdjacentHTML('beforeend', `
      <div class="card" style="margin-bottom:16px">
        <div style="display:flex;align-items:baseline;gap:16px;flex-wrap:wrap">
          <div>
            <div class="label" style="color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.06em">MTD (${fmt.htmlSafe(curLabel)})</div>
            <div style="font-family:var(--mono);font-size:22px;font-weight:500;color:var(--good)">${fmt.usd(compare.current_mtd)}</div>
          </div>
          <div style="color:var(--muted);font-size:16px;padding-bottom:2px">vs</div>
          <div>
            <div class="label" style="color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.06em">${fmt.htmlSafe(dayLabel)}</div>
            <div style="font-family:var(--mono);font-size:22px;font-weight:500">${fmt.usd(compare.previous_same_period)}</div>
          </div>
          <div style="font-family:var(--mono);font-size:18px;font-weight:600;color:${color};padding-bottom:2px">${fmt.htmlSafe(cpStr)}</div>
          <div class="spacer"></div>
          <div style="text-align:right">
            <div class="label" style="color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.06em">Projected (${fmt.htmlSafe(curLabel)})</div>
            <div style="font-family:var(--mono);font-size:18px;font-weight:500">${fmt.usd(compare.projected_month_end)}</div>
            <div style="color:var(--muted);font-size:11px">vs ${fmt.htmlSafe(prevLabel)} actual ${fmt.usd(compare.previous_month_total)}</div>
          </div>
        </div>
      </div>`);
  }

  // Bar chart — last 6 calendar months, current bar is lighter (partial)
  root.insertAdjacentHTML('beforeend', `
    <div class="card" style="margin-bottom:16px">
      <h3>Spend by calendar month</h3>
      <div id="ch-ll-monthly" style="height:220px"></div>
    </div>`);

  const curMonth = compare.current_mtd_month || '';
  const cats   = months.map(m => m.month);
  const values = months.map(m => ({ value: m.spend, itemStyle: { color: m.month === curMonth ? 'rgba(74,158,255,0.45)' : '#4A9EFF' } }));
  barChart(document.getElementById('ch-ll-monthly'), {
    categories: cats,
    values,
    color: '#4A9EFF',
  });

  // Model delta table (only if ≥2 months of data)
  if (hasData && deltas.length > 0) {
    const rows = deltas.map(d => {
      const sign  = d.delta >= 0 ? '+' : '';
      const color = Math.abs(d.delta) < 0.0001 ? 'var(--muted)' : (d.delta >= 0 ? 'var(--warn)' : 'var(--good)');
      return `<tr>
        <td><span class="badge ${fmt.modelClass(d.model)}">${fmt.htmlSafe(fmt.modelShort(d.model))}</span></td>
        <td class="num">${fmt.usd4(d.current_mtd_spend)}</td>
        <td class="num">${fmt.usd4(d.previous_same_period_spend)}</td>
        <td class="num" style="color:${color};font-family:var(--mono)">${sign}${fmt.usd4(Math.abs(d.delta))}</td>
      </tr>`;
    }).join('');

    root.insertAdjacentHTML('beforeend', `
      <div class="card" style="margin-bottom:16px">
        <h3>Model spend — this month MTD vs last month same period</h3>
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th class="num">This month MTD</th>
              <th class="num">Last month same period</th>
              <th class="num">Δ</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`);
  }

  // By-key table (hidden if no key data)
  if (keyDeltas.length > 0) {
    const keyRows = keyDeltas.map(k => {
      const sign  = k.delta >= 0 ? '+' : '';
      const color = Math.abs(k.delta) < 0.0001 ? 'var(--muted)' : (k.delta >= 0 ? 'var(--warn)' : 'var(--good)');
      return `<tr>
        <td class="mono">${fmt.htmlSafe(k.key_alias)}</td>
        <td class="num">${fmt.usd4(k.current_mtd_spend)}</td>
        <td class="num">${fmt.usd4(k.previous_same_period_spend)}</td>
        <td class="num" style="color:${color};font-family:var(--mono)">${sign}${fmt.usd4(Math.abs(k.delta))}</td>
      </tr>`;
    }).join('');

    root.insertAdjacentHTML('beforeend', `
      <div class="card" style="margin-bottom:16px">
        <h3>By key — this month MTD vs last month same period</h3>
        <table>
          <thead>
            <tr>
              <th>Key</th>
              <th class="num">This month MTD</th>
              <th class="num">Last month same period</th>
              <th class="num">Δ</th>
            </tr>
          </thead>
          <tbody>${keyRows}</tbody>
        </table>
      </div>`);
  }
}

// ── helpers ───────────────────────────────────────────────────────────────

function _relTime(isoStr) {
  try {
    const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
    if (diff < 60)   return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  } catch (_) { return isoStr; }
}
