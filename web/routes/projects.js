import { api, fmt, t } from '/web/app.js';

export default async function (root) {
  const rows = await api('/api/projects');
  root.innerHTML = `
    <div class="card">
      <h2>${t('pj_title')}</h2>
      <p class="muted" style="margin:-8px 0 14px">${t('pj_sub')}</p>
      <table>
        <thead><tr><th>${t('pj_col_project')}</th><th class="num">${t('pj_col_sessions')}</th><th class="num">${t('pj_col_turns')}</th><th class="num">${t('pj_col_billable')}</th><th class="num">${t('pj_col_cache')}</th></tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td title="${fmt.htmlSafe(r.project_slug)}">${fmt.htmlSafe(r.project_name || r.project_slug)}</td>
              <td class="num">${fmt.int(r.sessions)}</td>
              <td class="num">${fmt.int(r.turns)}</td>
              <td class="num">${fmt.int(r.billable_tokens)}</td>
              <td class="num">${fmt.int(r.cache_read_tokens)}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}
