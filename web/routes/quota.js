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
    ${renderInfo()}
  `;
}
