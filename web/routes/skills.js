import { api, fmt } from '/web/app.js';
import { groupedBarChart } from '/web/charts.js';

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
  const base = (location.hash.replace(/^#/, '').split('?')[0]) || '/skills';
  location.hash = '#' + base + '?range=' + encodeURIComponent(key);
}

function sinceIso(range) {
  if (!range.days) return null;
  return new Date(Date.now() - range.days * 86400 * 1000).toISOString();
}

export default async function (root) {
  const range = readRange();
  const since = sinceIso(range);
  const url = '/api/skills' + (since ? '?since=' + encodeURIComponent(since) : '');
  const skills = await api(url);

  const totalManual = skills.reduce((s, r) => s + r.manual_sessions, 0);
  const totalTool   = skills.reduce((s, r) => s + r.tool_invocations, 0);

  const rangeTabs = `
    <div class="range-tabs" role="tablist">
      ${RANGES.map(r => `<button data-range="${r.key}" class="${r.key === range.key ? 'active' : ''}">${r.label}</button>`).join('')}
    </div>`;

  root.innerHTML = `
    <div class="flex" style="margin-bottom:14px">
      <h2 style="margin:0;font-size:16px;letter-spacing:-0.01em">Skills &amp; Commands</h2>
      <span class="muted" style="font-size:12px">${range.days ? `last ${range.days} days` : 'all time'}</span>
      <div class="spacer"></div>
      ${rangeTabs}
    </div>

    <div class="row cols-3">
      <div class="card kpi"><div class="label">Unique skills / commands</div><div class="value">${fmt.int(skills.length)}</div></div>
      <div class="card kpi"><div class="label">You ran <span class="muted" style="font-weight:400;font-size:11px">(slash commands)</span></div><div class="value">${fmt.int(totalManual)}</div></div>
      <div class="card kpi"><div class="label">Claude invoked <span class="muted" style="font-weight:400;font-size:11px">(Skill tool)</span></div><div class="value">${fmt.int(totalTool)}</div></div>
    </div>

    <div class="card" style="margin-top:16px">
      <h3>Top skills &amp; commands</h3>
      <div id="ch-skills" style="height:320px"></div>
    </div>

    <div class="card" style="margin-top:16px">
      <h3>All skills &amp; commands</h3>
      <p class="muted" style="margin:-4px 0 14px;font-size:12px">
        <strong>You ran</strong> = sessions where you typed the slash command directly.
        <strong>Claude invoked</strong> = times Claude called the Skill tool mid-conversation.
        <strong>Skill tokens</strong> = size of the skill's <code>SKILL.md</code> loaded into context per invocation (slash commands show — as their file is not a registered skill).
      </p>
      <table>
        <thead><tr>
          <th>skill / command</th>
          <th class="num">you ran</th>
          <th class="num">claude invoked</th>
          <th class="num">skill tokens</th>
          <th>last used</th>
        </tr></thead>
        <tbody>
          ${skills.map(s => `
            <tr>
              <td><span class="badge">${fmt.htmlSafe(s.skill)}</span></td>
              <td class="num">${s.manual_sessions  ? fmt.int(s.manual_sessions)  : '<span class="muted">—</span>'}</td>
              <td class="num">${s.tool_invocations ? fmt.int(s.tool_invocations) : '<span class="muted">—</span>'}</td>
              <td class="num">${s.tokens_per_call == null ? '<span class="muted">—</span>' : fmt.int(s.tokens_per_call)}</td>
              <td class="mono">${fmt.ts(s.last_used)}</td>
            </tr>`).join('') || '<tr><td colspan="5" class="muted">no skills used in this range</td></tr>'}
        </tbody>
      </table>
    </div>
  `;

  root.querySelectorAll('.range-tabs button').forEach(btn => {
    btn.addEventListener('click', () => writeRange(btn.dataset.range));
  });

  const top = skills.slice(0, 12);
  groupedBarChart(document.getElementById('ch-skills'), {
    categories: top.map(t => t.skill.length > 26 ? t.skill.slice(0, 25) + '…' : t.skill),
    series: [
      { name: 'You ran',        values: top.map(t => t.manual_sessions),  color: '#3FB68B' },
      { name: 'Claude invoked', values: top.map(t => t.tool_invocations), color: '#7B61FF' },
    ],
  });
}
