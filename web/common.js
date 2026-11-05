'use strict';
(() => {
  const storageKey = 'canvas-tools-token';
  const token = location.hash.slice(1) || sessionStorage.getItem(storageKey) || '';
  if (location.hash) {
    sessionStorage.setItem(storageKey, token);
    history.replaceState(null, '', location.pathname);
  }
  const tool = location.pathname.startsWith('/logs/') ? 'logs' : location.pathname.startsWith('/files/') ? 'files' : '';
  let overview = null, closed = false;
  window.addEventListener('canvas-tools-closed', () => { closed = true; });
  const notice = document.getElementById('shared-notice');
  function message(text) { if (notice) { notice.textContent = text; notice.hidden = !text; } }
  async function api(path, payload) {
    const options = {headers: {Authorization: 'Bearer ' + token}, cache: 'no-store'};
    if (payload !== undefined) {
      options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(payload);
    }
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'The local app could not complete that action.');
    return data;
  }
  async function saveForm() {
    const url = document.getElementById('canvas-url'), output = document.getElementById('output'), browser = document.getElementById('browser');
    if (!tool || !url || !output || !browser || overview?.active_mode) return;
    await api('/api/settings', {canvas_url: url.value.trim(), output_base: output.value.trim(), browser: browser.value});
  }
  document.querySelectorAll('a[data-tool]').forEach(link => {
    const target = link.dataset.tool;
    if (target === tool) link.setAttribute('aria-current', 'page');
    link.addEventListener('click', async event => {
      event.preventDefault();
      if (overview?.active_mode && overview.active_mode !== target) {
        message('Finish or stop the current collection before switching tools.'); return;
      }
      try {
        await saveForm();
        await api('/api/select', {mode: target});
        location.assign(link.getAttribute('href'));
      } catch (error) { message(error.message); }
    });
  });
  for (const id of ['canvas-url', 'output', 'browser']) {
    document.getElementById(id)?.addEventListener('change', () => { saveForm().catch(() => {}); });
  }
  document.getElementById('quit-all')?.addEventListener('click', async () => {
    if (overview?.active_mode && !confirm('Stop the current collection and quit? Completed files will be preserved.')) return;
    try {
      await api('/api/quit', {}); closed = true;
      message('Local app closed. You can close this tab. Use Start App to reopen it.');
      document.querySelectorAll('a,button').forEach(element => element.setAttribute('aria-disabled', 'true'));
    } catch (error) { message(error.message); }
  });
  // Standalone collector regression tests can still serve the task at '/'.
  if (!token || (!tool && !document.getElementById('quit-all'))) return;
  async function poll() {
    if (closed) return;
    try {
      const value = await api('/api/state');
      if (value.tools) {
        overview = value;
        const active = overview.active_mode;
        for (const link of document.querySelectorAll('a[data-tool]')) {
          link.setAttribute('aria-disabled', String(!!active && active !== link.dataset.tool));
        }
        if (active && active !== tool) message('A collection is running in ' + (active === 'logs' ? 'Quiz activity logs' : 'Submitted files') + '. Open that tool to review or stop it.');
        else if (notice?.textContent.startsWith('A collection is running')) message('');
      }
    } catch (_) { message('The local app is unavailable. Use Start App to reopen it if it has closed.'); }
    setTimeout(poll, 750);
  }
  poll();
})();
