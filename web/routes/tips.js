import { api, fmt, t } from '/web/app.js';

export default async function (root) {
  const tips = await api('/api/tips');
  root.innerHTML = `
    <div class="card">
      <h2>${t('ti_title')}</h2>
      ${tips.length === 0
        ? `<p class="muted">${t('ti_empty')}</p>`
        : `<p class="muted" style="margin:-8px 0 14px">${t('ti_sub')}</p>`}
      ${tips.map(tip => `
        <div class="tip">
          <div class="tip-head">
            <span class="badge">${fmt.htmlSafe(tip.category)}</span>
            <strong>${fmt.htmlSafe(tip.title)}</strong>
            <span class="spacer"></span>
            <button class="ghost" data-key="${fmt.htmlSafe(tip.key)}">${t('ti_dismiss')}</button>
          </div>
          <p class="tip-body">${fmt.htmlSafe(tip.body)}</p>
        </div>`).join('')}
    </div>`;
  root.querySelectorAll('button[data-key]').forEach(b => {
    b.addEventListener('click', async () => {
      await fetch('/api/tips/dismiss', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: b.dataset.key }),
      });
      location.reload();
    });
  });
}
