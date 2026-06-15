import { api, fmt, state, t } from '/web/app.js';
import { barChart, donutChart, groupedBarChart, stackedBarChart } from '/web/charts.js';

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
  const base = (location.hash.replace(/^#/, '').split('?')[0]) || '/overview';
  location.hash = '#' + base + '?range=' + encodeURIComponent(key);
}

function sinceIso(range) {
  if (!range.days) return null;
  return new Date(Date.now() - range.days * 86400 * 1000).toISOString();
}

function withSince(url, since) {
  if (!since) return url;
  return url + (url.includes('?') ? '&' : '?') + 'since=' + encodeURIComponent(since);
}

export default async function (root) {
  const range = readRange();
  const since = sinceIso(range);

  const [totals, projects, sessions, tools, daily, byModel] = await Promise.all([
    api(withSince('/api/overview', since)),
    api(withSince('/api/projects', since)),
    api(withSince('/api/sessions?limit=10', since)),
    api(withSince('/api/tools', since)),
    api(withSince('/api/daily', since)),
    api(withSince('/api/by-model', since)),
  ]);

  const cacheCreate =
    (totals.cache_create_5m_tokens || 0) +
    (totals.cache_create_1h_tokens || 0);

  const kpi = (label, compactVal, fullVal, cls = '') => `
    <div class="card kpi ${cls}">
      <div class="label">${label}</div>
      <div class="value" title="${fullVal}">${compactVal}</div>
    </div>`;

  const rangeTabs = `
    <div class="range-tabs" role="tablist">
      ${RANGES.map(r => `<button data-range="${r.key}" class="${r.key === range.key ? 'active' : ''}">${r.key === 'all' ? t('range_all') : r.key}</button>`).join('')}
    </div>`;

  root.innerHTML = `
    <div class="flex" style="margin-bottom:14px">
      <h2 style="margin:0;font-size:16px;letter-spacing:-0.01em">${t('ov_title')}</h2>
      <span class="muted" style="font-size:12px">${range.days ? t('ov_last_days').replace('{d}', range.days) : t('ov_all_time')}</span>
      <div class="spacer"></div>
      ${rangeTabs}
    </div>

    <div class="row cols-7">
      ${kpi(t('kpi_sessions'),     fmt.int(totals.sessions),       fmt.int(totals.sessions))}
      ${kpi(t('kpi_turns'),        fmt.int(totals.turns),          fmt.int(totals.turns))}
      ${kpi(t('kpi_input'),        fmt.compact(totals.input_tokens),       fmt.int(totals.input_tokens) + ' tokens')}
      ${kpi(t('kpi_output'),       fmt.compact(totals.output_tokens),      fmt.int(totals.output_tokens) + ' tokens')}
      ${kpi(t('kpi_cache_read'),   fmt.compact(totals.cache_read_tokens),  fmt.int(totals.cache_read_tokens) + ' tokens')}
      ${kpi(t('kpi_cache_create'), fmt.compact(cacheCreate),               fmt.int(cacheCreate) + ' tokens')}
      <div class="card kpi cost">
        <div class="label">${t('kpi_cost')}</div>
        <div class="value" title="${fmt.usd(totals.cost_usd)}">${fmt.usd(totals.cost_usd)}</div>
        ${planSubtitle()}
      </div>
    </div>

    <details class="card glossary" style="margin-top:16px">
      <summary><h3 style="display:inline-block;margin:0">${t('ov_explain')}</h3><span class="muted" style="font-size:12px">${t('ov_expand')}</span></summary>
      <dl>
        <dt>${t('session_dt')}</dt><dd>${t('session_dd')}</dd>
        <dt>${t('turn_dt')}</dt><dd>${t('turn_dd')}</dd>
        <dt>${t('input_dt')}</dt><dd>${t('input_dd')}</dd>
        <dt>${t('output_dt')}</dt><dd>${t('output_dd')}</dd>
        <dt>${t('cread_dt')}</dt><dd>${t('cread_dd')}</dd>
        <dt>${t('ccreate_dt')}</dt><dd>${t('ccreate_dd')}</dd>
        <dt>${t('billable_dt')}</dt><dd>${t('billable_dd')}</dd>
      </dl>
    </details>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card">
        <h3>${t('daily_work')}</h3>
        <p class="muted" style="margin:-4px 0 10px;font-size:12px">${t('daily_work_sub')}</p>
        <div id="ch-daily-billable" style="height:260px"></div>
      </div>
      <div class="card">
        <h3>${t('daily_cache')}</h3>
        <p class="muted" style="margin:-4px 0 10px;font-size:12px">${t('daily_cache_sub')}</p>
        <div id="ch-daily-cache" style="height:260px"></div>
      </div>
    </div>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card"><h3>${t('by_project')}</h3><div id="ch-projects" style="height:320px"></div></div>
      <div class="card">
        <h3>${t('by_model')}</h3>
        <p class="muted" style="margin:-4px 0 4px;font-size:12px">${t('by_model_sub')}</p>
        <div id="ch-model" style="height:300px"></div>
      </div>
    </div>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card"><h3>${t('top_tools')}</h3><div id="ch-tools" style="height:320px"></div></div>
      <div class="card">
        <h3 style="display:flex;align-items:center"><span>${t('recent_sessions')}</span><span class="spacer"></span><a href="#/sessions" style="font-weight:400;font-size:12px">${t('all_arr')}</a></h3>
        <table>
          <thead><tr><th>${t('col_started')}</th><th>${t('col_project')}</th><th class="num">${t('col_tokens')}</th></tr></thead>
          <tbody>
            ${sessions.map(s => `
              <tr>
                <td class="mono">${fmt.ts(s.started)}</td>
                <td><a href="#/sessions/${encodeURIComponent(s.session_id)}">${fmt.htmlSafe(s.project_name || s.project_slug)}</a></td>
                <td class="num">${fmt.compact(s.tokens)}</td>
              </tr>`).join('') || `<tr><td colspan="3" class="muted">${t('no_sessions')}</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>
  `;

  // range buttons
  root.querySelectorAll('.range-tabs button').forEach(btn => {
    btn.addEventListener('click', () => writeRange(btn.dataset.range));
  });

  // Your daily work — billable tokens (input + output + cache create)
  stackedBarChart(document.getElementById('ch-daily-billable'), {
    categories: daily.map(d => d.day),
    series: [
      { name: t('leg_input'),  values: daily.map(d => d.input_tokens),        color: '#4A9EFF' },
      { name: t('leg_output'), values: daily.map(d => d.output_tokens),       color: '#7C5CFF' },
      { name: t('leg_cc'),     values: daily.map(d => d.cache_create_tokens), color: '#E8A23B' },
    ],
  });

  // Daily cache reads (separate — scale is 100× larger)
  stackedBarChart(document.getElementById('ch-daily-cache'), {
    categories: daily.map(d => d.day),
    series: [
      { name: t('leg_cr'), values: daily.map(d => d.cache_read_tokens), color: '#3FB68B' },
    ],
  });

  // by-model doughnut
  donutChart(document.getElementById('ch-model'),
    byModel.map(m => ({
      name: fmt.modelShort(m.model) || 'unknown',
      value: (m.input_tokens || 0) + (m.output_tokens || 0)
           + (m.cache_create_5m_tokens || 0) + (m.cache_create_1h_tokens || 0),
    })).filter(d => d.value > 0),
  );

  // tokens by project — input vs output
  const topProjects = projects.slice(0, 8);
  groupedBarChart(document.getElementById('ch-projects'), {
    categories: topProjects.map(p => {
      const name = p.project_name || p.project_slug;
      return name.length > 20 ? name.slice(0, 19) + '…' : name;
    }),
    series: [
      { name: t('leg_input'),  values: topProjects.map(p => p.input_tokens  || 0), color: '#4A9EFF' },
      { name: t('leg_output'), values: topProjects.map(p => p.output_tokens || 0), color: '#7C5CFF' },
    ],
  });

  // top tools
  const topTools = tools.slice(0, 8);
  barChart(document.getElementById('ch-tools'), {
    categories: topTools.map(t => t.tool_name),
    values: topTools.map(t => t.calls),
    color: '#7C5CFF',
  });
}

function planSubtitle() {
  if (!state.pricing || state.plan === 'api') return '';
  const p = state.pricing.plans[state.plan];
  if (!p || !p.monthly) return '';
  return `<div class="sub">${t('plan_pay').replace('{m}', p.monthly).replace('{l}', fmt.htmlSafe(p.label))}</div>`;
}
