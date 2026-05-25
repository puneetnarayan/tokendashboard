import { api, fmt, cacheGet, cacheSet } from '/web/app.js';

export default async function (root) {
  const url = '/api/lanes';
  let data = cacheGet(url);
  if (!data) {
    try { data = await api(url); cacheSet(url, data); }
    catch (e) {
      root.innerHTML = `<div class="card"><p class="muted">Lanes unavailable: ${fmt.htmlSafe(String(e))}</p></div>`;
      return;
    }
  }

  const dashboardUrl = data.dashboard_url || 'http://lanes.local.theinbtw.com';

  if (!data.total) {
    root.innerHTML = `
      <div class="card">
        <h2>Lane usage</h2>
        <p class="muted">No lane invocations yet — or the lane logs haven't synced to this host. Lane logs are pulled from Jstation every 5 min via cron.</p>
        <p style="margin-top:14px">
          <a href="${dashboardUrl}" target="_blank" rel="noopener" class="badge" style="padding:6px 12px;font-size:13px">
            Open full lane dashboard ↗
          </a>
        </p>
      </div>`;
    return;
  }

  const totalSec = Math.round(data.duration_ms_sum / 1000);
  const avgSec = data.total ? Math.round(totalSec / data.total) : 0;
  const errorPct = data.total ? Math.round(100 * data.errors / data.total) : 0;

  function listCard(title, rows, total) {
    return `
      <div class="card">
        <h3>${title}</h3>
        <table>
          <thead><tr><th>${title.toLowerCase()}</th><th class="num">calls</th><th class="num">share</th></tr></thead>
          <tbody>
            ${rows.slice(0, 10).map(r => `
              <tr>
                <td>${fmt.htmlSafe(r.key)}</td>
                <td class="num">${fmt.int(r.count)}</td>
                <td class="num muted">${((r.count / total) * 100).toFixed(0)}%</td>
              </tr>`).join('') || '<tr><td colspan="3" class="muted">no data</td></tr>'}
          </tbody>
        </table>
      </div>`;
  }

  root.innerHTML = `
    <div class="flex" style="margin-bottom:14px">
      <h2 style="margin:0">Lane usage</h2>
      <span class="muted" style="font-size:13px">multi-AI delegation via <code>/lanes</code></span>
      <div class="spacer"></div>
      <a href="${dashboardUrl}" target="_blank" rel="noopener" class="pill" style="font-family:var(--sans);text-decoration:none">
        Open full dashboard ↗
      </a>
    </div>

    <div class="row cols-4">
      <div class="card kpi">
        <div class="label">Total calls</div>
        <div class="value">${fmt.int(data.total)}</div>
      </div>
      <div class="card kpi">
        <div class="label">Errors</div>
        <div class="value">${fmt.int(data.errors)}</div>
        <div class="sub">${errorPct}% of calls</div>
      </div>
      <div class="card kpi">
        <div class="label">Total wall time</div>
        <div class="value">${formatDuration(totalSec)}</div>
        <div class="sub">avg ${avgSec}s/call</div>
      </div>
      <div class="card kpi">
        <div class="label">Tokens reported</div>
        <div class="value">${fmt.compact(data.tokens_used_sum)}</div>
        <div class="sub">when the AI reports usage</div>
      </div>
    </div>

    <div class="row cols-2" style="margin-top:16px">
      ${listCard('Lane', data.by_lane || [], data.total)}
      ${listCard('AI', data.by_ai || [], data.total)}
    </div>
    <div class="row cols-2" style="margin-top:16px">
      ${listCard('Machine', data.by_machine || [], data.total)}
      ${listCard('Model', data.by_model || [], data.total)}
    </div>

    <p class="muted" style="font-size:12px;margin-top:18px">
      Sources: ${(data.log_files || []).map(f => `<code>${fmt.htmlSafe(f.split('/').pop())}</code>`).join(' · ') || '(none)'}
      ${data.latest_ts ? ` · most recent call ${fmt.ts(data.latest_ts)}` : ''}
    </p>`;
}

function formatDuration(seconds) {
  if (seconds < 60) return seconds + 's';
  const m = Math.floor(seconds / 60);
  if (m < 60) return m + 'm';
  const h = Math.floor(m / 60);
  return h + 'h ' + (m % 60) + 'm';
}
