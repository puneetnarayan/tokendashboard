import { api, fmt } from '/web/app.js';

export default async function (root) {
  const servers = await api('/api/mcp');
  root.innerHTML = `
    <div class="flex" style="margin-bottom:14px">
      <h2 style="margin:0;font-size:16px;letter-spacing:-0.01em">MCP Servers</h2>
      <span class="muted" style="font-size:12px">${servers.length} configured</span>
    </div>
    <div class="row cols-3">
      ${servers.map(s => `
        <div class="card">
          <h3>${fmt.htmlSafe(s.name)}
            <span class="badge">${fmt.htmlSafe(s.kind)}</span>
          </h3>
          <p class="muted" style="font-size:12px;min-height:32px">${fmt.htmlSafe(s.description || 'no description')}</p>
          <p style="font-size:12px">status: ${fmt.htmlSafe(s.status || '—')}</p>
          ${s.usage_calls != null ? `<p style="font-size:12px">${fmt.int(s.usage_calls)} calls (all time)</p>` : ''}
          ${s.file_path
            ? `<button data-open="${fmt.htmlSafe(s.file_path)}">Open config in editor</button>`
            : `<p class="muted" style="font-size:11px">account-level — manage via claude.ai → Settings → Connectors</p>`}
        </div>
      `).join('')}
    </div>
  `;
  root.querySelectorAll('[data-open]').forEach(btn => {
    btn.addEventListener('click', () => { window.location.href = fmt.editorLink(btn.dataset.open); });
  });
}
