import { api, fmt } from '/web/app.js';
import { stackedBarChart } from '/web/charts.js';

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
  const base = (location.hash.replace(/^#/, '').split('?')[0]) || '/subagents';
  location.hash = '#' + base + '?range=' + encodeURIComponent(key);
}

function sinceIso(range) {
  if (!range.days) return null;
  return new Date(Date.now() - range.days * 86400 * 1000).toISOString();
}

export default async function (root) {
  const range = readRange();
  const since = sinceIso(range);
  const qs = since ? '?since=' + encodeURIComponent(since) : '';
  const data = await api('/api/subagents' + qs);
  const rows = data.breakdown;
  const tops = data.top_sessions;

  const models = Array.from(new Set(rows.map(r => r.model))).sort();
  const mainByModel = Object.fromEntries(models.map(m => [m, 0]));
  const subByModel  = Object.fromEntries(models.map(m => [m, 0]));
  const mainSessions = new Set();
  const subSessions  = new Set();
  let totalSubMsgs = 0;
  let totalMainMsgs = 0;
  let totalSubCost = 0;
  let totalMainCost = 0;
  rows.forEach(r => {
    const io = (r.input_tokens || 0) + (r.output_tokens || 0);
    if (r.is_sidechain) {
      subByModel[r.model] = io;
      totalSubMsgs += r.messages;
      totalSubCost += r.cost_usd || 0;
    } else {
      mainByModel[r.model] = io;
      totalMainMsgs += r.messages;
      totalMainCost += r.cost_usd || 0;
    }
  });

  const rangeTabs = `
    <div class="range-tabs" role="tablist">
      ${RANGES.map(r => `<button data-range="${r.key}" class="${r.key === range.key ? 'active' : ''}">${r.label}</button>`).join('')}
    </div>`;

  const subPct = (totalMainMsgs + totalSubMsgs) > 0
    ? (totalSubMsgs / (totalMainMsgs + totalSubMsgs) * 100).toFixed(0) + '%'
    : '—';

  root.innerHTML = `
    <div class="flex" style="margin-bottom:14px">
      <h2 style="margin:0;font-size:16px;letter-spacing:-0.01em">Subagents</h2>
      <span class="muted" style="font-size:12px">${range.days ? `last ${range.days} days` : 'all time'}</span>
      <div class="spacer"></div>
      ${rangeTabs}
    </div>

    <div class="row cols-3">
      <div class="card kpi"><div class="label">Subagent share of all msgs</div><div class="value">${subPct}</div></div>
      <div class="card kpi"><div class="label">Subagent messages</div><div class="value">${fmt.int(totalSubMsgs)}</div></div>
      <div class="card kpi"><div class="label">Subagent cost (est.)</div><div class="value">${fmt.usd(totalSubCost)}</div></div>
    </div>

    <div class="card" style="margin-top:16px">
      <h3>Per-model split — main thread vs subagent (Task tool)</h3>
      <p class="muted" style="margin:-4px 0 14px;font-size:12px">
        Token Dashboard's <code>/api/by-model</code> aggregates both. This view splits them so you can see e.g. how much Sonnet usage is subagent-only, or whether Haiku is doing its share of the cheap work.
      </p>
      <div id="ch-subagents" style="height:340px"></div>
    </div>

    <div class="card" style="margin-top:16px">
      <h3>Per-model breakdown table</h3>
      <table>
        <thead><tr>
          <th>model</th>
          <th>where</th>
          <th class="num">messages</th>
          <th class="num">sessions</th>
          <th class="num">input + output</th>
          <th class="num">cache read</th>
          <th class="num">cost (est.)</th>
        </tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td><span class="badge model-${fmt.modelClass(r.model)}">${fmt.htmlSafe(fmt.modelShort(r.model))}</span></td>
              <td>${r.is_sidechain ? '<span class="badge">subagent</span>' : '<span class="muted">main</span>'}</td>
              <td class="num">${fmt.int(r.messages)}</td>
              <td class="num">${fmt.int(r.sessions)}</td>
              <td class="num">${fmt.int((r.input_tokens || 0) + (r.output_tokens || 0))}</td>
              <td class="num">${fmt.int(r.cache_read_tokens)}</td>
              <td class="num">${fmt.usd(r.cost_usd)}${r.cost_estimated ? ' <span class="muted">~</span>' : ''}</td>
            </tr>`).join('') || '<tr><td colspan="7" class="muted">no assistant turns in this range</td></tr>'}
        </tbody>
      </table>
    </div>

    <div class="card" style="margin-top:16px">
      <h3>Top sessions by subagent token spend</h3>
      <p class="muted" style="margin:-4px 0 14px;font-size:12px">
        Click a row to drill in. <em>Dispatch-tree caveat:</em> the parent-Opus→child-subagent linkage isn't reconstructable from the current Claude Code transcript schema (sidechain messages' <code>parent_uuid</code> points within the subagent thread, not back to the dispatching prompt). We surface aggregate model presence per session instead.
      </p>
      <table>
        <thead><tr>
          <th>project</th>
          <th>session</th>
          <th class="num">subagent msgs</th>
          <th class="num">i/o tokens</th>
          <th class="num">cache read</th>
          <th>models seen</th>
        </tr></thead>
        <tbody>
          ${tops.length === 0 ? '<tr><td colspan="6" class="muted">no subagent activity in this range</td></tr>' : tops.map(t => `
            <tr class="clickable" data-session="${fmt.htmlSafe(t.session_id)}">
              <td>${fmt.htmlSafe(t.project_name || t.project_slug)}</td>
              <td class="mono" style="font-size:11px" title="${fmt.htmlSafe(t.session_id)}">${fmt.htmlSafe(t.session_id.slice(0, 8))}…</td>
              <td class="num">${fmt.int(t.subagent_msgs)}</td>
              <td class="num">${fmt.int(t.io_tokens)}</td>
              <td class="num">${fmt.int(t.cache_read_tokens)}</td>
              <td>${(t.models || []).map(m => `<span class="badge model-${fmt.modelClass(m)}">${fmt.htmlSafe(fmt.modelShort(m))}</span>`).join(' ')}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>
  `;

  root.querySelectorAll('.range-tabs button').forEach(btn => {
    btn.addEventListener('click', () => writeRange(btn.dataset.range));
  });
  root.querySelectorAll('tr.clickable').forEach(tr => {
    tr.style.cursor = 'pointer';
    tr.addEventListener('click', () => {
      location.hash = '#/sessions/' + tr.dataset.session;
    });
  });

  stackedBarChart(document.getElementById('ch-subagents'), {
    categories: models.map(m => fmt.modelShort(m)),
    series: [
      { name: 'main',     values: models.map(m => mainByModel[m] || 0), color: '#4A9EFF' },
      { name: 'subagent', values: models.map(m => subByModel[m]  || 0), color: '#7C5CFF' },
    ],
  });
}
