import { api, fmt, cacheGet, cacheSet, showToast } from '/web/app.js';

export default async function (root) {
  const qs = new URLSearchParams((location.hash.split('?')[1] || ''));
  const q = qs.get('q') || '';
  root.innerHTML = `
    <div class="card">
      <h2 style="display:flex;align-items:center;gap:12px">
        <span>Search</span>
        <input id="search-input" type="search" placeholder="prompt text… (FTS5 syntax OK: foo AND bar, &quot;exact phrase&quot;, prefix*)" value="${fmt.htmlSafe(q)}" autofocus
          style="flex:1;background:var(--panel-2);border:1px solid var(--border);color:var(--text);padding:8px 12px;border-radius:6px;font-size:14px;font-family:var(--sans)" />
      </h2>
      <p class="muted" style="font-size:13px;margin:0 0 14px">
        Searches every user prompt you've sent across all sessions. Click a result to jump into that session.
      </p>
      <div id="search-results"></div>
    </div>`;

  const input = root.querySelector('#search-input');
  const out = root.querySelector('#search-results');
  let debounce = null;

  function setHash(qv) {
    const base = '/search';
    location.hash = '#' + base + (qv ? '?q=' + encodeURIComponent(qv) : '');
  }
  input.addEventListener('input', () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => setHash(input.value.trim()), 220);
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { clearTimeout(debounce); setHash(input.value.trim()); }
  });

  if (q) renderResults(out, q); else renderEmpty(out);
}

async function renderResults(out, q) {
  out.innerHTML = '<div class="muted" style="padding:12px">Searching…</div>';
  const url = '/api/search?q=' + encodeURIComponent(q) + '&limit=80';
  let data = cacheGet(url);
  if (!data) {
    try { data = await api(url); cacheSet(url, data); }
    catch (e) {
      out.innerHTML = `<div class="muted">search failed: ${fmt.htmlSafe(String(e))}</div>`;
      return;
    }
  }
  if (data.error) {
    out.innerHTML = `<div class="muted">syntax error: ${fmt.htmlSafe(data.error)}</div>`;
    return;
  }
  const results = data.results || [];
  if (!results.length) {
    out.innerHTML = `<div class="muted" style="padding:18px;text-align:center">No prompts match <b>${fmt.htmlSafe(q)}</b>. Try a different spelling, broaden the term, or use <code>foo OR bar</code>.</div>`;
    return;
  }
  out.innerHTML = `
    <div class="muted" style="font-size:13px;margin-bottom:8px">${results.length} hit${results.length === 1 ? '' : 's'}</div>
    <table>
      <thead><tr><th>when</th><th>host</th><th>project</th><th>snippet</th></tr></thead>
      <tbody>
        ${results.map(r => `
          <tr>
            <td class="mono" style="white-space:nowrap">${fmt.ts(r.timestamp)}</td>
            <td><span class="host-pill host-${fmt.htmlSafe(r.host || 'unknown')}">${fmt.htmlSafe(r.host || '—')}</span></td>
            <td><a href="#/sessions/${encodeURIComponent(r.session_id)}">${fmt.htmlSafe((r.project_slug || '').replace(/^-/, ''))}</a></td>
            <td class="blur-sensitive">${r.snippet || ''}</td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

function renderEmpty(out) {
  out.innerHTML = `
    <div class="muted" style="padding:24px;text-align:center;line-height:1.7">
      Type a query above and hit Enter (or wait a beat).<br>
      Examples: <code>mutagen</code> · <code>"database is locked"</code> · <code>refactor AND scanner</code> · <code>tweak*</code>
    </div>`;
}
