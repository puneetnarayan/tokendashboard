import { api, fmt, state, cacheGet, cacheSet } from '/web/app.js';
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
  const url   = withSince('/api/overview-bundle', since);

  // Single render — cache hit is instant, cache miss hits warm server cache.
  // No background prefetch: when the cache was just cleared (refresh / SSE
  // scan event), prefetching all 4 ranges in parallel slammed the server
  // with 4 cold-cache bundle queries simultaneously. Server's own warming
  // threads cover this without piling on from the client.
  const cached = cacheGet(url);
  if (cached) { renderBundle(root, cached, range); return; }
  const fresh = await api(url);
  cacheSet(url, fresh);
  renderBundle(root, fresh, range);
}

function renderBundle(root, bundle, range) {
  const { totals, prev_totals, projects, sessions, tools, daily, byModel } = bundle;
  const cacheCreate =
    (totals.cache_create_5m_tokens || 0) +
    (totals.cache_create_1h_tokens || 0);
  const prevCacheCreate = prev_totals
    ? (prev_totals.cache_create_5m_tokens || 0) + (prev_totals.cache_create_1h_tokens || 0)
    : null;

  // Period-over-period delta chip. Returns empty when there's no prev
  // value to compare to (e.g. all-time view) or both sides are zero.
  function delta(curr, prev) {
    if (prev === null || prev === undefined) return '';
    const c = Number(curr) || 0;
    const p = Number(prev) || 0;
    if (c === 0 && p === 0) return '';
    if (p === 0) return `<div class="delta up">new</div>`;
    const pct = ((c - p) / p) * 100;
    const cls = pct > 0 ? 'up' : pct < 0 ? 'down' : '';
    const sign = pct > 0 ? '+' : '';
    return `<div class="delta ${cls}">${sign}${pct.toFixed(0)}% vs prev</div>`;
  }

  function kpi(label, curr, compactVal, fullVal, prev, cls = '') {
    return `
      <div class="card kpi ${cls}">
        <div class="label">${label}</div>
        <div class="value" title="${fullVal}">${compactVal}</div>
        ${delta(curr, prev)}
      </div>`;
  }

  const rangeTabs = `
    <div class="range-tabs" role="tablist">
      ${RANGES.map(r => `<button data-range="${r.key}" class="${r.key === range.key ? 'active' : ''}">${r.label}</button>`).join('')}
    </div>`;

  root.innerHTML = `
    <div class="flex" style="margin-bottom:14px">
      <h2 style="margin:0;font-size:18px;letter-spacing:-0.01em">Overview</h2>
      <span class="muted" style="font-size:13px">${range.days ? `last ${range.days} days` : 'all time'}</span>
      <div class="spacer"></div>
      ${rangeTabs}
    </div>

    <div class="row cols-8">
      ${kpi('Sessions',     totals.sessions,           fmt.int(totals.sessions),           fmt.int(totals.sessions),                              prev_totals && prev_totals.sessions)}
      ${kpi('Turns',        totals.turns,              fmt.int(totals.turns),              fmt.int(totals.turns),                                 prev_totals && prev_totals.turns)}
      ${kpi('Input',        totals.input_tokens,       fmt.compact(totals.input_tokens),   fmt.int(totals.input_tokens) + ' tokens',              prev_totals && prev_totals.input_tokens)}
      ${kpi('Output',       totals.output_tokens,      fmt.compact(totals.output_tokens),  fmt.int(totals.output_tokens) + ' tokens',             prev_totals && prev_totals.output_tokens)}
      ${kpi('Cache read',   totals.cache_read_tokens,  fmt.compact(totals.cache_read_tokens), fmt.int(totals.cache_read_tokens) + ' tokens',     prev_totals && prev_totals.cache_read_tokens)}
      ${kpi('Cache create', cacheCreate,               fmt.compact(cacheCreate),           fmt.int(cacheCreate) + ' tokens',                      prevCacheCreate)}
      ${kpi('Active time',  totals.active_seconds || 0, fmt.duration(totals.active_seconds || 0), fmt.duration(totals.active_seconds || 0) + ' hands-on', prev_totals && prev_totals.active_seconds, 'active')}
      <div class="card kpi cost">
        <div class="label">Est. cost</div>
        <div class="value" title="${fmt.usd(totals.cost_usd)}">${fmt.usd(totals.cost_usd)}</div>
        ${delta(totals.cost_usd, prev_totals && prev_totals.cost_usd) || planSubtitle()}
      </div>
    </div>

    <div class="card" id="heatmap-card" style="margin-top:16px">
      <h3 style="display:flex;align-items:center;gap:10px">
        <span>When you use Claude</span>
        <span class="spacer"></span>
        <span class="muted" style="font-size:12px" id="heatmap-caption">last 365 days · darker = more tokens</span>
        <span class="heatmap-toggle" role="group" aria-label="Heatmap metric">
          <button type="button" data-metric="tokens" class="heatmap-btn is-active">Tokens</button>
          <button type="button" data-metric="hours"  class="heatmap-btn">Hours</button>
        </span>
      </h3>
      <div id="ch-heatmap" style="overflow-x:auto"></div>
    </div>

    <details class="card glossary" style="margin-top:16px">
      <summary><h3 style="display:inline-block;margin:0">What do these numbers mean?</h3><span class="muted" style="font-size:13px">— click to expand</span></summary>
      <dl>
        <dt>Session</dt><dd>One run of Claude Code (from <code>claude</code> to exit). Each session is a single <code>.jsonl</code> file.</dd>
        <dt>Turn</dt><dd>One message you sent to Claude. Each turn triggers a response (possibly with tool calls in between).</dd>
        <dt>Input tokens</dt><dd>The new text you (and tool results) sent to Claude this turn. Billed at the full input rate.</dd>
        <dt>Output tokens</dt><dd>The text Claude wrote back. Billed at the highest rate — usually the biggest cost driver per turn.</dd>
        <dt>Cache read</dt><dd>Tokens Claude re-used from a cache (your CLAUDE.md, previously-read files, the conversation so far). ~10× cheaper than fresh input. High cache-read counts = good cost hygiene.</dd>
        <dt>Cache create</dt><dd>Writing something into the cache for the first time. One-time cost; pays off on the next turn.</dd>
        <dt>Billable tokens</dt><dd>Input + Output + Cache create. Cache reads are billed separately (and much cheaper).</dd>
      </dl>
    </details>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card">
        <h3>Your daily work</h3>
        <p class="muted" style="margin:-4px 0 10px;font-size:13px">Tokens you paid for: what you sent (<b>input</b>), what Claude wrote (<b>output</b>), and what got stored for re-use (<b>cache create</b>).</p>
        <div id="ch-daily-billable" style="height:260px"></div>
      </div>
      <div class="card">
        <h3>Daily cache reads</h3>
        <p class="muted" style="margin:-4px 0 10px;font-size:13px"><b>Cache reads</b> are cheap re-uses of things Claude already saw (like your CLAUDE.md). They cost ~10× less than regular input tokens — high numbers here are a good thing.</p>
        <div id="ch-daily-cache" style="height:260px"></div>
      </div>
    </div>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card"><h3>Tokens by project</h3><div id="ch-projects" style="height:320px"></div></div>
      <div class="card">
        <h3>Token usage by model</h3>
        <p class="muted" style="margin:-4px 0 4px;font-size:13px">Share of billable tokens per Claude model.</p>
        <div id="ch-model" style="height:300px"></div>
      </div>
    </div>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card"><h3>Top tools (by call count)</h3><div id="ch-tools" style="height:320px"></div></div>
      <div class="card">
        <h3 style="display:flex;align-items:center"><span>Recent sessions</span><span class="spacer"></span><a href="#/sessions" style="font-weight:400;font-size:13px">all →</a></h3>
        <table>
          <thead><tr><th>started</th><th>project</th><th class="num">tokens</th></tr></thead>
          <tbody>
            ${sessions.map(s => `
              <tr>
                <td class="mono">${fmt.ts(s.started)}</td>
                <td><a href="#/sessions/${encodeURIComponent(s.session_id)}">${fmt.htmlSafe(s.project_name || s.project_slug)}</a></td>
                <td class="num">${fmt.compact(s.tokens)}</td>
              </tr>`).join('') || '<tr><td colspan="3" class="muted">no sessions in this range</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>
  `;

  root.querySelectorAll('.range-tabs button').forEach(btn => {
    btn.addEventListener('click', () => writeRange(btn.dataset.range));
  });

  // Calendar heatmap — independent of the range tabs (it's always "last
  // 365 days" so the user can see when they actually code regardless of
  // which range is selected for the KPIs/charts).
  const heatHost = root.querySelector('#ch-heatmap');
  const heatCaption = root.querySelector('#heatmap-caption');
  let heatMetric = 'tokens';
  renderHeatmap(heatHost, heatMetric, heatCaption);
  root.querySelectorAll('.heatmap-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      heatMetric = btn.dataset.metric;
      root.querySelectorAll('.heatmap-btn').forEach(b =>
        b.classList.toggle('is-active', b.dataset.metric === heatMetric)
      );
      renderHeatmap(heatHost, heatMetric, heatCaption);
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

  const topProjects = projects.slice(0, 8);
  groupedBarChart(document.getElementById('ch-projects'), {
    categories: topProjects.map(p => {
      const name = p.project_name || p.project_slug;
      return name.length > 20 ? name.slice(0, 19) + '…' : name;
    }),
    series: [
      { name: 'input',  values: topProjects.map(p => p.input_tokens  || 0), color: '#4A9EFF' },
      { name: 'output', values: topProjects.map(p => p.output_tokens || 0), color: '#7C5CFF' },
    ],
  });

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
  return `<div class="sub">pay $${p.monthly}/mo on ${fmt.htmlSafe(p.label)}</div>`;
}

// ── Calendar heatmap (last 365 days) ─────────────────────────────────────────
async function renderHeatmap(host, metric = 'tokens', caption = null) {
  if (!host) return;
  const since = new Date(Date.now() - 365 * 86400_000).toISOString();
  const cacheKey = '/api/daily?since=' + encodeURIComponent(since);
  let daily = cacheGet(cacheKey);
  if (!daily) {
    daily = await api(cacheKey);
    cacheSet(cacheKey, daily);
  }
  // Same data feeds both modes — only the value-extractor + tooltip
  // formatter change. Cached payload now ships `active_seconds`.
  const extract = metric === 'hours'
    ? d => d.active_seconds || 0
    : d => (d.input_tokens || 0) + (d.output_tokens || 0);
  const fmtVal = metric === 'hours'
    ? v => `${(v / 3600).toFixed(1)} h`
    : v => `${v.toLocaleString()} tokens`;
  if (caption) {
    caption.textContent = metric === 'hours'
      ? 'last 365 days · darker = more hands-on time · concurrent sessions sum'
      : 'last 365 days · darker = more tokens';
  }

  const totalsByDay = new Map(daily.map(d => [d.day, extract(d)]));
  const today = new Date();
  // Start on the most-recent Sunday so week columns are uniform.
  const end = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const startOffset = (end.getDay() + 1) % 7; // back up to Saturday-end-of-week
  end.setDate(end.getDate() + (6 - end.getDay()));
  const start = new Date(end);
  start.setDate(start.getDate() - 52 * 7 - 6);
  // 53 weeks × 7 days grid.
  const cellSize = 11, cellGap = 3;
  const cells = [];
  const months = [];
  let lastMonth = -1;
  const max = Math.max(1, ...totalsByDay.values());
  for (let w = 0; w < 53; w++) {
    for (let d = 0; d < 7; d++) {
      const day = new Date(start);
      day.setDate(start.getDate() + w * 7 + d);
      if (day > end) continue;
      const iso = day.toISOString().slice(0, 10);
      const v = totalsByDay.get(iso) || 0;
      const intensity = v === 0 ? 0 : Math.max(0.2, Math.min(1, Math.log10(v + 1) / Math.log10(max + 1)));
      const fill = v === 0 ? 'rgba(255,255,255,0.04)' : `rgba(74,158,255,${intensity.toFixed(2)})`;
      const x = w * (cellSize + cellGap);
      const y = d * (cellSize + cellGap);
      cells.push(`<rect x="${x}" y="${y}" width="${cellSize}" height="${cellSize}" rx="2" fill="${fill}"><title>${iso} · ${fmtVal(v)}</title></rect>`);
    }
    // Month label above the first column of each new month.
    const probe = new Date(start);
    probe.setDate(start.getDate() + w * 7 + 6);
    if (probe.getMonth() !== lastMonth) {
      months.push(`<text x="${w * (cellSize + cellGap)}" y="-4" fill="#5A6573" font-size="10" font-family="Inter">${probe.toLocaleString('en', { month: 'short' })}</text>`);
      lastMonth = probe.getMonth();
    }
  }
  const dayLabels = ['Mon', 'Wed', 'Fri'].map((lbl, i) => {
    const y = (i * 2 + 1) * (cellSize + cellGap) + cellSize - 2;
    return `<text x="-20" y="${y}" fill="#5A6573" font-size="10" font-family="Inter">${lbl}</text>`;
  }).join('');
  const w = 53 * (cellSize + cellGap) + 26;
  const h = 7 * (cellSize + cellGap) + 18;
  host.innerHTML = `<svg width="${w}" height="${h}" viewBox="-26 -14 ${w} ${h}" xmlns="http://www.w3.org/2000/svg">
    ${months.join('')}${dayLabels}${cells.join('')}
  </svg>`;
}
