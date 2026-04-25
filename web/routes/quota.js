import { fmt } from '/web/app.js';

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
        <section class="card">
          <h3>Plan usage</h3>
          <p class="muted small">Couldn't fetch live plan usage: ${fmt.htmlSafe(err.error || ('HTTP ' + r.status))}.</p>
          <p class="muted small">Make sure you've authenticated with Claude Code (run <code>/login</code>) and have an active session. If the dashboard's stored OAuth tokens were rotated by Claude Code, delete the <code>pu_*</code> rows from the <code>plan</code> table to force re-bootstrap.</p>
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
    <section class="card">
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
        Mirrors the Plan usage panel from Claude Code's <code>/status</code> screen,
        fetched live from Anthropic's <code>/api/oauth/usage</code> endpoint using the
        OAuth tokens bootstrapped from your macOS Keychain. Cached for 60 seconds.
      </p>
    </section>
  `;
}

export default async function (root) {
  root.innerHTML = '<div class="loading">Loading plan usage…</div>';
  root.innerHTML = `
    <h2>Plan usage</h2>
    ${await renderPlanUsage()}
    ${renderInfo()}
  `;
}
