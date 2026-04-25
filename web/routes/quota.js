import { api, fmt } from '/web/app.js';

function modelClass(modelId) {
  const s = (modelId || '').toLowerCase();
  if (s.includes('opus'))   return 'opus';
  if (s.includes('sonnet')) return 'sonnet';
  if (s.includes('haiku'))  return 'haiku';
  return '';
}

function modelLabel(payload) {
  if (!payload?.model) return 'unknown';
  return payload.model.display_name || payload.model.id || 'unknown';
}

function contextStats(payload) {
  const cw = payload?.context_window;
  if (!cw) return null;
  const usage = cw.current_usage || {};
  const input = usage.input_tokens || 0;
  const cacheRead = usage.cache_read_input_tokens || 0;
  const total = input + cacheRead;
  // used_percentage is sometimes pre-calculated by Claude Code; trust it if present
  const pct = typeof cw.used_percentage === 'number' ? cw.used_percentage : null;
  return { input, cacheRead, total, pct };
}

function progressBar(pct, label) {
  const safePct = pct == null ? 0 : Math.min(100, Math.max(0, pct));
  const cls = safePct >= 70 ? 'critical' : safePct >= 50 ? 'warning' : 'ok';
  return `
    <div class="progress-row">
      <div class="progress-bar ${cls}">
        <div class="progress-fill" style="width:${safePct.toFixed(1)}%"></div>
      </div>
      <div class="progress-label">${label}</div>
    </div>
  `;
}

function noDataPanel() {
  return `
    <div class="card">
      <h3>No Claude Code activity captured yet</h3>
      <p class="muted">
        The dashboard mirrors what Claude Code's statusline reports. Run any
        Claude Code prompt — the statusline hook (<code>~/.claude/context-status.sh</code>)
        will write the JSON payload to <code>~/.claude/last-status.json</code> and the
        dashboard will pick it up here.
      </p>
    </div>
  `;
}

function renderCurrent(current) {
  if (!current) return noDataPanel();
  const stats = contextStats(current);
  const model = modelLabel(current);
  const mc = modelClass(current.model?.id);
  const totalK = stats ? Math.round(stats.total / 1000) : 0;
  const pct = stats?.pct;
  const label = pct == null
    ? `${fmt.int(stats?.total ?? 0)} tokens (no % from Claude)`
    : `${pct.toFixed(0)}% — ${totalK}k tokens in context`;

  return `
    <section class="card">
      <h3>Current context — <span class="badge model-${mc}">${fmt.htmlSafe(model)}</span></h3>
      ${progressBar(pct, label)}
      <p class="muted small" style="margin-top:8px">
        Mirrored from Claude Code's statusline. Updates each turn.
        Input tokens: ${fmt.int(stats?.input ?? 0)} ·
        Cache read: ${fmt.int(stats?.cacheRead ?? 0)}
      </p>
    </section>
  `;
}

function renderHistory(history) {
  if (!history.length) return '';
  // Build a simple sparkline-style bar chart of context % over time
  const points = history
    .map(e => {
      const p = e.payload?.context_window;
      const pct = typeof p?.used_percentage === 'number' ? p.used_percentage : null;
      return { ts: e.ts, pct, model: e.payload?.model?.id };
    })
    .filter(p => p.pct != null);

  if (!points.length) return '';

  const max = Math.max(100, ...points.map(p => p.pct));
  const bars = points.map(p => {
    const h = (p.pct / max) * 100;
    const cls = p.pct >= 70 ? 'critical' : p.pct >= 50 ? 'warning' : 'ok';
    const title = `${(p.pct).toFixed(0)}% at ${p.ts.slice(11, 19)} (${(p.model || '').replace('claude-', '')})`;
    return `<div class="spark-bar ${cls}" style="height:${h.toFixed(1)}%" title="${fmt.htmlSafe(title)}"></div>`;
  }).join('');

  return `
    <section class="card" style="margin-top:16px">
      <h3>Context fill — last ${points.length} turns</h3>
      <div class="sparkline">${bars}</div>
      <p class="muted small">Each bar = one turn. Hover for value.</p>
    </section>
  `;
}

const PLAN_USAGE_LABELS = {
  five_hour: '5-hour limit',
  seven_day: 'Weekly · all models',
  seven_day_sonnet: 'Sonnet only',
  seven_day_opus: 'Opus only',
  seven_day_omelette: 'Weekly · custom workspace',
  seven_day_oauth_apps: 'OAuth apps',
  seven_day_cowork: 'Cowork',
};

const PLAN_USAGE_ORDER = [
  'five_hour', 'seven_day', 'seven_day_sonnet',
  'seven_day_opus', 'seven_day_omelette',
  'seven_day_oauth_apps', 'seven_day_cowork',
];

function formatResets(resetsAt) {
  if (!resetsAt) return '';
  const ms = new Date(resetsAt).getTime() - Date.now();
  if (isNaN(ms) || ms < 0) return 'now';
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hrs = Math.floor(min / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  return `${days}d`;
}

function planUsageRow(key, entry) {
  if (!entry) return '';
  const label = PLAN_USAGE_LABELS[key] || key;
  const pct = entry.utilization ?? 0;
  const cls = pct >= 70 ? 'critical' : pct >= 50 ? 'warning' : 'ok';
  const resets = entry.resets_at ? `· resets ${formatResets(entry.resets_at)}` : '';
  return `
    <div class="plan-row">
      <div class="plan-label">${fmt.htmlSafe(label)}</div>
      <div class="plan-bar"><div class="plan-fill ${cls}" style="width:${Math.min(100, pct).toFixed(1)}%"></div></div>
      <div class="plan-pct">${pct.toFixed(0)}% <span class="muted">${fmt.htmlSafe(resets)}</span></div>
    </div>
  `;
}

async function renderPlanUsage() {
  let payload;
  try {
    const r = await fetch('/api/plan_usage');
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      return `
        <section class="card" style="margin-top:16px">
          <h3>Plan usage</h3>
          <p class="muted small">Couldn't fetch live plan usage: ${fmt.htmlSafe(err.error || ('HTTP ' + r.status))}.</p>
          <p class="muted small">Make sure you've authenticated with Claude Code (run <code>/login</code>) and have an active session.</p>
        </section>
      `;
    }
    payload = await r.json();
  } catch (e) {
    return `<section class="card"><h3>Plan usage</h3><p class="muted small">Network error: ${fmt.htmlSafe(e.message)}</p></section>`;
  }

  const data = payload.data || {};
  const fetchedAgo = Math.max(0, Math.floor((Date.now() - (payload.fetched_at || 0)) / 1000));
  const cachedNote = payload.cached ? ` (cached ${fetchedAgo}s ago)` : '';

  const rows = PLAN_USAGE_ORDER
    .map(k => planUsageRow(k, data[k]))
    .filter(Boolean)
    .join('');

  const extra = data.extra_usage;
  let extraHtml = '';
  if (extra && extra.is_enabled) {
    const credits = extra.used_credits ?? 0;
    extraHtml = `<p class="muted small" style="margin-top:8px">Extra usage: ${credits} ${fmt.htmlSafe(extra.currency || '')} used${extra.monthly_limit ? ' / ' + extra.monthly_limit + ' limit' : ''}</p>`;
  }

  return `
    <section class="card" style="margin-top:16px">
      <h3>Plan usage <span class="muted small" style="font-weight:normal">live from Anthropic${fmt.htmlSafe(cachedNote)}</span></h3>
      ${rows || '<p class="muted">No active limits reported.</p>'}
      ${extraHtml}
    </section>
  `;
}

function renderInfo() {
  return `
    <section class="card" style="margin-top:16px">
      <h3>About this view</h3>
      <p class="muted small">
        This tab mirrors Claude Code's own context-window indicator (the same
        data the statusline shows). It does <strong>not</strong> reflect your
        Anthropic plan's 5-hour or weekly rate-limit quotas — those are
        tracked by Anthropic, not exposed locally. Run <code>/status</code>
        inside Claude Code for the authoritative plan-quota view.
      </p>
    </section>
  `;
}

export default async function (root) {
  root.innerHTML = '<div class="loading">Loading statusline mirror…</div>';
  const data = await api('/api/statusline');
  root.innerHTML = `
    <h2>Context</h2>
    ${renderCurrent(data.current)}
    ${renderHistory(data.history || [])}
    ${await renderPlanUsage()}
    ${renderInfo()}
  `;
}
