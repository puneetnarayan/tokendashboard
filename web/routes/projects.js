import { api, fmt } from '/web/app.js';

export default async function (root) {
  const slug = location.hash.replace(/^#\/projects\//, '').split('?')[0];
  if (slug && slug !== '/projects') {
    return renderDetail(root, decodeURIComponent(slug));
  }

  const rows = await api('/api/projects');
  root.innerHTML = `
    <div class="card">
      <h2>Projects</h2>
      <p class="muted" style="margin:-8px 0 14px">Sorted by billable token spend. Click a project to drill in.</p>
      <table>
        <thead><tr><th>project</th><th class="num">sessions</th><th class="num">turns</th><th class="num">billable tokens</th><th class="num">cache reads</th></tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr style="cursor:pointer" onclick="location.hash='#/projects/${encodeURIComponent(r.project_slug)}'">
              <td title="${fmt.htmlSafe(r.project_slug)}"><a href="#/projects/${encodeURIComponent(r.project_slug)}" onclick="event.stopPropagation()">${fmt.htmlSafe(r.project_name || r.project_slug)}</a></td>
              <td class="num">${fmt.int(r.sessions)}</td>
              <td class="num">${fmt.int(r.turns)}</td>
              <td class="num">${fmt.int(r.billable_tokens)}</td>
              <td class="num">${fmt.int(r.cache_read_tokens)}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

async function renderDetail(root, slug) {
  const enc = encodeURIComponent(slug);
  const [totals, tools, skills, sessions] = await Promise.all([
    api(`/api/overview?project=${enc}`),
    api(`/api/tools?project=${enc}`),
    api(`/api/skills?project=${enc}`),
    api(`/api/sessions?limit=10&project=${enc}`),
  ]);

  const cacheCreate = (totals.cache_create_5m_tokens || 0) + (totals.cache_create_1h_tokens || 0);
  const cacheTotal  = (totals.cache_read_tokens || 0) + (totals.input_tokens || 0) + cacheCreate;
  const cacheHit    = cacheTotal > 0 ? totals.cache_read_tokens / cacheTotal : null;

  const kpi = (label, val, sub = '') => `
    <div class="card kpi">
      <div class="label">${label}</div>
      <div class="value">${val}</div>
      ${sub ? `<div class="sub">${sub}</div>` : ''}
    </div>`;

  root.innerHTML = `
    <div style="margin-bottom:14px">
      <a href="#/projects" style="font-size:13px;opacity:.7">← Projects</a>
      <h2 style="margin:6px 0 0;font-size:18px">${fmt.htmlSafe(slug)}</h2>
    </div>

    <div class="row cols-5" style="margin-bottom:16px">
      ${kpi('Sessions',    fmt.int(totals.sessions))}
      ${kpi('Turns',       fmt.int(totals.turns))}
      ${kpi('Input',       fmt.compact(totals.input_tokens), 'tokens')}
      ${kpi('Output',      fmt.compact(totals.output_tokens), 'tokens')}
      ${kpi('Cache hit',   cacheHit != null ? fmt.pct(cacheHit) : '—', cacheHit != null && cacheHit < 0.4 ? '<span style="color:#f87">low — target >40%</span>' : 'of input')}
    </div>

    <div class="row cols-2" style="margin-bottom:16px">
      <div class="card">
        <h3>Tools</h3>
        <table>
          <thead><tr><th>tool</th><th class="num">calls</th><th class="num">result tokens</th></tr></thead>
          <tbody>
            ${tools.length ? tools.map(t => `
              <tr>
                <td>${fmt.htmlSafe(t.tool_name)}</td>
                <td class="num">${fmt.int(t.calls)}</td>
                <td class="num">${fmt.compact(t.result_tokens)}</td>
              </tr>`).join('') : '<tr><td colspan="3" class="muted">no tool calls</td></tr>'}
          </tbody>
        </table>
      </div>

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
    </div>

    <div class="card">
      <h3>Recent sessions <a href="#/sessions" style="font-weight:400;font-size:12px;float:right">all sessions →</a></h3>
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
    </div>`;
}
