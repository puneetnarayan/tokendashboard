import { fmt } from '/web/app.js';

// ─── helpers ──────────────────────────────────────────────────────────────

function tierLabel(rt) {
  const M = {
    'default_claude_max_5x': 'Max (5x)',
    'default_claude_max_20x': 'Max (20x)',
    'default_claude_pro': 'Pro',
    'claude_team': 'Team',
    'claude_enterprise': 'Enterprise',
  };
  return M[rt] || (rt || 'Unknown plan');
}

function formatResetsRelative(iso) {
  if (!iso) return '';
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return 'Resets now';
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `Resets in ${sec} sec`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `Resets in ${min} min`;
  const hr = Math.floor(min / 60);
  const remMin = min % 60;
  if (hr < 24) return `Resets in ${hr} hr${remMin ? ' ' + remMin + ' min' : ''}`;
  const day = Math.floor(hr / 24);
  return `Resets in ${day} day${day === 1 ? '' : 's'}`;
}

function formatResetsWeekday(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const wd = d.toLocaleDateString('en-US', { weekday: 'short' });
  const t = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
  return `Resets ${wd} ${t}`;
}

function formatResetsDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return `Resets ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`;
}

function lastUpdatedRelative(ts) {
  if (!ts) return '';
  const sec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (sec < 5) return 'just now';
  if (sec < 60) return `${sec} sec ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} minute${min === 1 ? '' : 's'} ago`;
  const hr = Math.floor(min / 60);
  return `${hr} hr ago`;
}

// ─── row builder ──────────────────────────────────────────────────────────

function usageRow({ label, sublabel, infoTip, pct, rightText, max = 100 }) {
  const safePct = Math.min(100, Math.max(0, ((pct ?? 0) * 100) / max));
  const tip = infoTip ? `<span class="usage-info" title="${fmt.htmlSafe(infoTip)}">i</span>` : '';
  return `
    <div class="usage-row">
      <div class="usage-label-block">
        <div class="usage-label">${fmt.htmlSafe(label)} ${tip}</div>
        ${sublabel ? `<div class="usage-sublabel">${fmt.htmlSafe(sublabel)}</div>` : ''}
      </div>
      <div class="usage-bar-wrap"><div class="usage-bar"><div class="usage-bar-fill" style="width:${safePct.toFixed(1)}%"></div></div></div>
      <div class="usage-right">${fmt.htmlSafe(rightText || '')}</div>
    </div>
  `;
}

// ─── data fetch ───────────────────────────────────────────────────────────

async function fetchData(force = false) {
  const r = await fetch('/api/plan_usage' + (force ? '?force=1' : ''));
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.error || ('HTTP ' + r.status));
  }
  return r.json();
}

// ─── panel composition ──────────────────────────────────────────────────--

function buildPanel(payload) {
  const usage = payload.data?.usage || {};
  const profile = payload.data?.profile || {};
  const tier = tierLabel(profile.organization?.rate_limit_tier);

  // Plan usage limits — current 5-hour session
  const fiveHour = usage.five_hour;
  const planLimitsHtml = fiveHour
    ? usageRow({
        label: 'Current session',
        sublabel: formatResetsRelative(fiveHour.resets_at),
        pct: fiveHour.utilization,
        rightText: `${Math.round(fiveHour.utilization)}% used`,
      })
    : '<div class="usage-row muted">No current-session data</div>';

  // Weekly limits
  const weeklyEntries = [
    ['seven_day', 'All models', null],
    ['seven_day_sonnet', 'Sonnet only', 'Weekly cap dedicated to Sonnet usage'],
    ['seven_day_opus', 'Opus only', 'Weekly cap dedicated to Opus usage'],
    ['seven_day_omelette', 'Claude Design', 'Custom workspace allocation'],
    ['seven_day_oauth_apps', 'OAuth apps', null],
    ['seven_day_cowork', 'Cowork', null],
  ];
  const weeklyHtml = weeklyEntries
    .map(([k, label, tip]) => {
      const e = usage[k];
      if (!e) return '';
      const sub = (k === 'seven_day_omelette' && e.utilization === 0)
        ? "You haven't used Claude Design yet"
        : formatResetsWeekday(e.resets_at);
      return usageRow({
        label,
        sublabel: sub,
        infoTip: tip,
        pct: e.utilization,
        rightText: `${Math.round(e.utilization)}% used`,
      });
    })
    .filter(Boolean)
    .join('');

  // Last updated meta
  const lastUpdated = lastUpdatedRelative(payload.fetched_at);
  const lastUpdatedHtml = `
    <div class="usage-meta">
      <span class="muted">Last updated: ${fmt.htmlSafe(lastUpdated)}</span>
      <button class="usage-refresh" type="button" title="Refresh now">⟳</button>
    </div>
  `;

  // Additional features (claude.ai-only data — show as unavailable)
  const additionalHtml = `
    <div class="usage-row muted">
      <div class="usage-label-block">
        <div class="usage-label">Daily included routine runs <span class="usage-info" title="Available on claude.ai/settings/usage">i</span></div>
        <div class="usage-sublabel">Available on claude.ai/settings/usage</div>
      </div>
      <div class="usage-bar-wrap"><div class="usage-bar"><div class="usage-bar-fill" style="width:0%"></div></div></div>
      <div class="usage-right">— / —</div>
    </div>
  `;

  // Extra usage section
  const extra = usage.extra_usage || {};
  const orgHasExtra = !!profile.organization?.has_extra_usage_enabled;
  const isOn = orgHasExtra && extra.is_enabled !== false;
  const spentEur = (extra.used_credits ?? 0) / 100;
  const monthlyLimit = extra.monthly_limit;
  const monthlyLimitDisplay = monthlyLimit
    ? `${(monthlyLimit / 100).toFixed(2)} ${extra.currency || ''}`.trim()
    : 'Unlimited';
  const sevenDayResetsAt = usage.seven_day?.resets_at;

  const extraHtml = `
    <div class="usage-extra-toggle-row">
      <div class="usage-extra-desc">Turn on extra usage to keep using Claude if you hit a limit. <a href="https://support.anthropic.com" target="_blank" rel="noopener">Learn more</a></div>
      <div class="usage-toggle ${isOn ? 'on' : 'off'}" title="Toggle on claude.ai/settings/billing"><span class="usage-toggle-knob"></span></div>
    </div>
    <div class="usage-row">
      <div class="usage-label-block">
        <div class="usage-value">€${spentEur.toFixed(2)} spent</div>
        <div class="usage-sublabel">${fmt.htmlSafe(formatResetsDate(sevenDayResetsAt))}</div>
      </div>
      <div></div>
      <div class="usage-right"></div>
    </div>
    <div class="usage-row">
      <div class="usage-label-block">
        <div class="usage-value">${fmt.htmlSafe(monthlyLimitDisplay)}</div>
        <div class="usage-sublabel">Monthly spend limit</div>
      </div>
      <div></div>
      <div class="usage-right"><a class="usage-button" href="https://claude.ai/settings/billing" target="_blank" rel="noopener">Adjust limit</a></div>
    </div>
    <div class="usage-row">
      <div class="usage-label-block">
        <div class="usage-value">View on claude.ai</div>
        <div class="usage-sublabel">Current balance · Auto-reload (only on claude.ai)</div>
      </div>
      <div></div>
      <div class="usage-right"><a class="usage-button primary" href="https://claude.ai/settings/billing" target="_blank" rel="noopener">Buy extra usage</a></div>
    </div>
  `;

  return `
    <div class="usage-panel">
      <div class="usage-section-head">
        <h3 class="usage-section-title">Plan usage limits</h3>
        <span class="usage-tier-badge">${fmt.htmlSafe(tier)}</span>
      </div>
      ${planLimitsHtml}

      <hr class="usage-divider">

      <div class="usage-section-head">
        <h3 class="usage-section-title">Weekly limits</h3>
      </div>
      <div class="usage-section-sub"><a href="https://support.anthropic.com" target="_blank" rel="noopener">Learn more about usage limits</a></div>
      ${weeklyHtml}
      ${lastUpdatedHtml}

      <hr class="usage-divider">

      <div class="usage-section-head">
        <h3 class="usage-section-title">Additional features</h3>
      </div>
      ${additionalHtml}

      <hr class="usage-divider">

      <div class="usage-section-head">
        <h3 class="usage-section-title">Extra usage</h3>
      </div>
      ${extraHtml}
    </div>
  `;
}

// ─── default export ──────────────────────────────────────────────────────-

export default async function (root) {
  async function render() {
    root.innerHTML = '<div class="loading">Loading plan usage…</div>';
    try {
      const payload = await fetchData();
      root.innerHTML = buildPanel(payload);
      wireRefresh();
    } catch (e) {
      root.innerHTML = `
        <div class="usage-panel">
          <div class="usage-section-head"><h3 class="usage-section-title">Plan usage</h3></div>
          <p class="muted">Couldn't load plan usage: ${fmt.htmlSafe(e.message)}.</p>
          <p class="muted small">If you've recently used Claude Code in another window, the OAuth refresh tokens may have rotated. Delete the <code>pu_*</code> rows from <code>~/.claude/token-dashboard.db</code> <code>plan</code> table and reload to re-bootstrap.</p>
        </div>
      `;
    }
  }

  function wireRefresh() {
    const btn = root.querySelector('.usage-refresh');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = '⟳ refreshing…';
      try {
        const fresh = await fetchData(true);
        root.innerHTML = buildPanel(fresh);
        wireRefresh();
      } catch (e) {
        alert('Refresh failed: ' + e.message);
        btn.disabled = false;
        btn.textContent = '⟳';
      }
    });
  }

  await render();
}
