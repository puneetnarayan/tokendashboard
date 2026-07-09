import { api, fmt, state } from '/web/app.js';
import { hBarChart, donutChart, stackedBarChart } from '/web/charts.js';

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

function sinceIso(range) {
  if (!range.days) return null;
  return new Date(Date.now() - range.days * 86400 * 1000).toISOString();
}

function qs(base, params) {
  const parts = Object.entries(params).filter(([, v]) => v != null).map(([k, v]) => `${k}=${encodeURIComponent(v)}`);
  return parts.length ? `${base}?${parts.join('&')}` : base;
}

export default async function (root) {
  const hashPath = location.hash.replace(/^#/, '');
  const slug = hashPath.startsWith('/projects/')
    ? decodeURIComponent(hashPath.slice('/projects/'.length).split('?')[0])
    : null;
  if (slug) {
    return renderDetail(root, slug);
  }

  const range = readRange();
  const since = sinceIso(range);
  const rows = await api(qs('/api/projects', { since }));

  const rangeTabs = `
    <div class="range-tabs" role="tablist">
      ${RANGES.map(r => `<button data-range="${r.key}" class="${r.key === range.key ? 'active' : ''}">${r.label}</button>`).join('')}
    </div>`;

  root.innerHTML = `
    <div class="flex" style="margin-bottom:14px;align-items:baseline">
      <h2 style="margin:0;font-size:16px;letter-spacing:-0.01em">Projects</h2>
      <span class="muted" style="font-size:12px">${range.days ? `last ${range.days} days` : 'all time'}</span>
      <div class="spacer"></div>
      ${rangeTabs}
    </div>
    <div class="card">
      <p class="muted" style="margin:0 0 14px">Sorted by billable token spend. Click a project to drill in.</p>
      <table>
        <thead><tr><th>project</th><th class="num">sessions</th><th class="num">turns</th><th class="num">billable tokens</th><th class="num">cache reads</th><th class="num">est. cost</th></tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr style="cursor:pointer" onclick="location.hash='#/projects/${encodeURIComponent(r.project_slug)}'">
              <td title="${fmt.htmlSafe(r.project_slug)}"><a href="#/projects/${encodeURIComponent(r.project_slug)}" onclick="event.stopPropagation()">${fmt.htmlSafe(r.project_name || r.project_slug)}</a></td>
              <td class="num">${fmt.int(r.sessions)}</td>
              <td class="num">${fmt.int(r.turns)}</td>
              <td class="num">${fmt.int(r.billable_tokens)}</td>
              <td class="num">${fmt.int(r.cache_read_tokens)}</td>
              <td class="num">${r.cost_usd != null ? fmt.usd(r.cost_usd) : '—'}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;

  root.querySelectorAll('.range-tabs button').forEach(btn => {
    btn.addEventListener('click', () => {
      location.hash = '#/projects?range=' + btn.dataset.range;
    });
  });
}

async function renderDetail(root, slug) {
  const enc = encodeURIComponent(slug);
  const range = readRange();
  const since = sinceIso(range);

  const [totals, tools, skills, sessions, daily, byModel] = await Promise.all([
    api(qs('/api/overview',  { project: enc, since })),
    api(qs('/api/tools',     { project: enc, since })),
    api(qs('/api/skills',    { project: enc, since })),
    api(qs('/api/sessions',  { project: enc, since, limit: 10 })),
    api(qs('/api/daily',     { project: enc, since })),
    api(qs('/api/by-model',  { project: enc, since })),
  ]);

  const cacheCreate = (totals.cache_create_5m_tokens || 0) + (totals.cache_create_1h_tokens || 0);
  const cacheTotal  = (totals.cache_read_tokens || 0) + (totals.input_tokens || 0) + cacheCreate;
  const cacheHit    = cacheTotal > 0 ? totals.cache_read_tokens / cacheTotal : null;

  const costUsd = byModel.reduce((sum, m) => sum + (m.cost_usd || 0), 0);

  const kpi = (label, compact, full, cls = '') => `
    <div class="card kpi ${cls}">
      <div class="label">${label}</div>
      <div class="value" title="${full}">${compact}</div>
    </div>`;

  const rangeTabs = `
    <div class="range-tabs" role="tablist">
      ${RANGES.map(r => `<button data-range="${r.key}" class="${r.key === range.key ? 'active' : ''}">${r.label}</button>`).join('')}
    </div>`;

  root.innerHTML = `
    <div class="flex" style="margin-bottom:14px;align-items:baseline">
      <a href="#/projects" style="font-size:13px;opacity:.7;margin-right:12px">← Projects</a>
      <h2 style="margin:0;font-size:16px;letter-spacing:-0.01em">${fmt.htmlSafe(slug)}</h2>
      <span class="muted" style="font-size:12px">${range.days ? `last ${range.days} days` : 'all time'}</span>
      <div class="spacer"></div>
      ${rangeTabs}
    </div>

    <div class="row cols-7">
      ${kpi('Sessions',     fmt.int(totals.sessions),      fmt.int(totals.sessions))}
      ${kpi('Turns',        fmt.int(totals.turns),         fmt.int(totals.turns))}
      ${kpi('Input',        fmt.compact(totals.input_tokens),      fmt.int(totals.input_tokens) + ' tokens')}
      ${kpi('Output',       fmt.compact(totals.output_tokens),     fmt.int(totals.output_tokens) + ' tokens')}
      ${kpi('Cache read',   fmt.compact(totals.cache_read_tokens), fmt.int(totals.cache_read_tokens) + ' tokens')}
      ${kpi('Cache hit',    cacheHit != null ? fmt.pct(cacheHit) : '—', cacheHit != null ? (cacheHit * 100).toFixed(1) + '%' : '—', cacheHit != null && cacheHit < 0.4 ? 'warn' : '')}
      <div class="card kpi cost">
        <div class="label">Est. cost</div>
        <div class="value" title="${fmt.usd(costUsd)}">${fmt.usd(costUsd)}</div>
      </div>
    </div>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card">
        <h3>Daily work</h3>
        <p class="muted" style="margin:-4px 0 10px;font-size:12px">Input + output + cache create — what you paid for.</p>
        <div id="ch-daily-billable" style="height:240px"></div>
      </div>
      <div class="card">
        <h3>Daily cache reads</h3>
        <p class="muted" style="margin:-4px 0 10px;font-size:12px">Cache reads cost ~10× less — high numbers are good.</p>
        <div id="ch-daily-cache" style="height:240px"></div>
      </div>
    </div>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card">
        <h3>Token usage by model</h3>
        <div id="ch-model" style="height:220px"></div>
        <table style="margin-top:12px">
          <thead><tr><th>model</th><th class="num">billable tokens</th><th class="num">est. cost</th></tr></thead>
          <tbody>
            ${byModel.filter(m => (m.input_tokens||0)+(m.output_tokens||0)+(m.cache_create_5m_tokens||0)+(m.cache_create_1h_tokens||0) > 0).map(m => `
              <tr>
                <td><span class="badge">${fmt.htmlSafe(fmt.modelShort(m.model) || m.model)}</span></td>
                <td class="num">${fmt.compact((m.input_tokens||0)+(m.output_tokens||0)+(m.cache_create_5m_tokens||0)+(m.cache_create_1h_tokens||0))}</td>
                <td class="num">${m.cost_usd != null ? fmt.usd(m.cost_usd) : '—'}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
      <div class="card" style="align-self:flex-start">
        <h3>Top tools (by call count)</h3>
        <div id="ch-tools"></div>
      </div>
    </div>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card">
        <h3>Skills</h3>
        <table>
          <thead><tr><th>skill</th><th class="num">invocations</th><th class="num">sessions</th><th>last used</th></tr></thead>
          <tbody>
            ${skills.length ? skills.map(s => `
              <tr>
                <td>${fmt.htmlSafe(s.skill)}</td>
                <td class="num">${fmt.int(s.invocations)}</td>
                <td class="num">${fmt.int(s.sessions)}</td>
                <td class="mono" style="font-size:11px">${fmt.ts(s.last_used)}</td>
              </tr>`).join('') : '<tr><td colspan="4" class="muted">no skill invocations</td></tr>'}
          </tbody>
        </table>
      </div>

      <div class="card">
        <h3>Recent sessions</h3>
        <table>
          <thead><tr><th>started</th><th class="num">turns</th><th class="num">tokens</th></tr></thead>
          <tbody>
            ${sessions.length ? sessions.map(s => `
              <tr>
                <td class="mono">${fmt.ts(s.started)}</td>
                <td class="num">${fmt.int(s.turns)}</td>
                <td class="num">${fmt.compact(s.tokens)}</td>
              </tr>`).join('') : '<tr><td colspan="3" class="muted">no sessions</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>
  `;

  root.querySelectorAll('.range-tabs button').forEach(btn => {
    btn.addEventListener('click', () => {
      const base = location.hash.replace(/^#/, '').split('?')[0];
      location.hash = '#' + base + '?range=' + btn.dataset.range;
    });
  });

  stackedBarChart(document.getElementById('ch-daily-billable'), {
    categories: daily.map(d => d.day),
    series: [
      { name: 'input',        values: daily.map(d => d.input_tokens),        color: '#4A9EFF' },
      { name: 'output',       values: daily.map(d => d.output_tokens),       color: '#7C5CFF' },
      { name: 'cache create', values: daily.map(d => d.cache_create_tokens), color: '#E8A23B' },
    ],
  });

  stackedBarChart(document.getElementById('ch-daily-cache'), {
    categories: daily.map(d => d.day),
    series: [
      { name: 'cache read', values: daily.map(d => d.cache_read_tokens), color: '#3FB68B' },
    ],
  });

  donutChart(document.getElementById('ch-model'),
    byModel.map(m => ({
      name: fmt.modelShort(m.model) || 'unknown',
      value: (m.input_tokens || 0) + (m.output_tokens || 0)
           + (m.cache_create_5m_tokens || 0) + (m.cache_create_1h_tokens || 0),
    })).filter(d => d.value > 0),
  );

  const topTools = tools.slice(0, 8);
  const toolsEl = document.getElementById('ch-tools');
  toolsEl.style.height = Math.max(160, topTools.length * 34) + 'px';
  hBarChart(toolsEl, {
    categories: topTools.map(t => t.tool_name),
    values: topTools.map(t => t.calls),
    color: '#7C5CFF',
  });
}
