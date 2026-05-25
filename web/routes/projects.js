import { api, fmt, makeSortable, cacheGet, cacheSet } from '/web/app.js';

const URL = '/api/projects';

export default async function (root) {
  const cached = cacheGet(URL);
  if (cached) { renderProjects(root, cached); return; }

  const fresh = await api(URL);
  cacheSet(URL, fresh);
  renderProjects(root, fresh);
}

function renderProjects(root, rows) {
  root.innerHTML = `
    <div class="card">
      <h2>Projects</h2>
      <p class="muted" style="margin:-8px 0 14px">Click any column header to sort. Rows roll up sub-paths and merge the same project across hosts; hover a row to see contributing slugs.</p>
      <table id="projects-table">
        <thead><tr><th>project</th><th>hosts</th><th class="num">sessions</th><th class="num">turns</th><th class="num" title="Hands-on time (10-min idle threshold)">active</th><th class="num">billable tokens</th><th class="num">cache reads</th></tr></thead>
        <tbody>
          ${rows.map(r => renderRow(r)).join('')}
        </tbody>
      </table>
    </div>`;
  makeSortable(root.querySelector('#projects-table'), { col: 5, dir: 'desc' });
}

function renderRow(r) {
  const name = r.project_name || r.project_slug;
  const slugs = Array.isArray(r.contributing_slugs) ? r.contributing_slugs : [r.project_slug];
  const subCount = slugs.length - 1;
  const hosts = Array.isArray(r.hosts) ? r.hosts : [];
  const tooltip = slugs.length > 1
    ? `Rolled up ${slugs.length} slugs:\n  ${slugs.join('\n  ')}`
    : r.project_slug || '';
  const subBadge = subCount > 0
    ? `<span class="muted" style="font-size:11px;margin-left:6px" title="${fmt.htmlSafe(slugs.join(' · '))}">+${subCount} sub${subCount === 1 ? '' : 's'}</span>`
    : '';
  const hostPills = hosts.length
    ? hosts.map(h => `<span class="host-pill host-${fmt.htmlSafe(h)}">${fmt.htmlSafe(h)}</span>`).join(' ')
    : '<span class="muted" style="font-size:11px">—</span>';

  const activeSec = r.active_seconds || 0;
  return `
    <tr>
      <td data-val="${fmt.htmlSafe(name)}" title="${fmt.htmlSafe(tooltip)}">${fmt.htmlSafe(name)}${subBadge}</td>
      <td data-val="${fmt.htmlSafe(hosts.join(','))}">${hostPills}</td>
      <td class="num" data-val="${r.sessions || 0}">${fmt.int(r.sessions)}</td>
      <td class="num" data-val="${r.turns || 0}">${fmt.int(r.turns)}</td>
      <td class="num" data-val="${activeSec}" title="Active time with a 10-min idle threshold. Concurrent sessions in two terminals double-count.">${fmt.duration(activeSec)}</td>
      <td class="num" data-val="${r.billable_tokens || 0}">${fmt.int(r.billable_tokens)}</td>
      <td class="num" data-val="${r.cache_read_tokens || 0}">${fmt.int(r.cache_read_tokens)}</td>
    </tr>`;
}
