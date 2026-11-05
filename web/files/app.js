'use strict';
const $ = id => document.getElementById(id);
const token = location.hash.slice(1) || sessionStorage.getItem('canvas-tools-token') || '';
if (location.hash) {
  sessionStorage.setItem('canvas-tools-token', token);
  history.replaceState(null, '', location.pathname);
}
let state = null, initialized = false, busy = false, closed = false, pickerDefaultsKey = '';
const terminal = new Set(['idle', 'complete', 'stopped', 'error']);
const stages = {
  idle: ['Set up a collection', 'Complete the Set up a collection fields, then select Open Canvas.'],
  opening: ['Opening Canvas', 'Wait for the separate Canvas window to open.'],
  navigate: ['Open SpeedGrader', ''],
  connecting: ['Connecting to SpeedGrader', 'The app is checking the loaded page locally.'],
  pick_student: ['Click the current student’s name field', ''],
  picker_retry: ['Try the name selection again', 'The browser stays open while you correct the setup.'],
  learning: ['Click the submitted file link', ''],
  checking: ['Check the first downloaded file', ''],
  verifying: ['Checking the automatic click', 'The app is verifying that its automatic download matches the first file.'],
  running: ['Downloading remaining files', 'The app moves through students automatically. Leave the Canvas window alone.'],
  stopping: ['Stopping', 'Waiting for the current browser operation to finish. Saved files will be kept.'],
  complete: ['Collection finished', 'Use Open download folder to review your saved files.'],
  stopped: ['Collection stopped', 'Your completed downloads are preserved.'],
  error: ['Collection needs attention', 'Completed files are preserved. Check the message below before starting again.']
};
async function api(path, payload) {
  const options = {headers: {'Authorization': 'Bearer ' + token}, cache: 'no-store'};
  if (payload !== undefined) {
    options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(payload);
  }
  const response = await fetch((location.pathname.startsWith('/files/') ? '/files' : '') + path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The local app could not complete that action.');
  return result;
}
function showError(message) { $('local-error').textContent = message; $('local-error').hidden = !message; }
async function action(name, extra = {}) {
  if (busy || !state) return;
  busy = true; showError('');
  try {
    await api('/api/action', {action: name, run_id: state.run_id, ...extra});
    await refresh();
  } catch (error) { showError(error.message); }
  finally { busy = false; }
}
function render(value) {
  state = value;
  if (!initialized) {
    for (const [id, key] of [['canvas-url','canvas_url'], ['output','output_base'], ['extension','extension'], ['browser','browser']]) $(id).value = value[key];
    initialized = true;
  }
  const phase = value.phase, active = !terminal.has(phase);
  const restartRequired = value.name_click_setup_supported !== true;
  $('update-note').hidden = !restartRequired;
  if (!active && value.picker_saved !== undefined && $('browser').value === value.browser && $('canvas-url').value === value.canvas_url) {
    const key = JSON.stringify([value.browser, value.canvas_url, value.picker_saved]);
    if (key !== pickerDefaultsKey) {
      $('relearn').checked = !value.picker_saved;
      $('name-setup').open = !value.picker_saved;
      pickerDefaultsKey = key;
    }
    $('picker-hint').textContent = value.picker_saved
      ? 'A name field is saved for this Canvas site and browser. Check the box to choose it again.'
      : 'No name field is saved for this Canvas site and browser. The app will ask you to click the current student’s name field.';
  }
  const [title, description] = stages[phase] || stages.error;
  $('stage-title').textContent = title; $('stage-description').textContent = description;
  $('stage-description').hidden = !description;
  $('status').textContent = title; $('version').textContent = 'Version ' + (value.app_version || value.version);
  for (const [id, expected] of [['navigate','navigate'], ['pick-student','pick_student'], ['picker-retry','picker_retry'], ['learning','learning'], ['checking','checking']]) $(id).hidden = phase !== expected;
  $('download-location-note').hidden = value.browser !== 'msedge' || !['learning','checking','verifying','running'].includes(phase);
  $('error').textContent = value.error + (value.error_code ? ' Error code: ' + value.error_code : '');
  $('error').hidden = !value.error;
  $('stop').hidden = !active; $('stop').disabled = phase === 'stopping';
  for (const element of $('setup').elements) element.disabled = active || restartRequired;
  $('launch').textContent = phase === 'idle' ? 'Open Canvas' : 'Open Canvas for a new collection';
  $('progress-section').hidden = !value.saved && !value.skipped && phase !== 'running';
  $('saved-count').textContent = value.saved + (value.saved === 1 ? ' file saved' : ' files saved');
  $('skip-count').textContent = value.skipped + ' skipped';
  $('progress-note').textContent = active ? 'Keep the Canvas window open and leave it alone while downloads run.' : 'Completed downloads are kept in the folder below.';
  $('output-section').hidden = !value.first_file;
  $('output-path').textContent = value.output;
  $('open-first').disabled = !value.first_file;
  if (phase !== 'checking') $('reviewed').checked = false;
  $('checked').disabled = !$('reviewed').checked;
  const step = phase === 'idle' ? 0 : ['opening','navigate','connecting'].includes(phase) ? 1 : ['pick_student','picker_retry'].includes(phase) ? 2 : phase === 'learning' ? 3 : phase === 'checking' ? 4 : 5;
  for (let i = 1; i <= 5; i++) {
    $('step' + i).classList.toggle('active', i === step);
    $('step' + i).classList.toggle('done', i < step);
    if (i === step) $('step' + i).setAttribute('aria-current', 'step');
    else $('step' + i).removeAttribute('aria-current');
  }
}
async function refresh() { render(await api('/api/state')); }
$('setup').addEventListener('submit', event => {
  event.preventDefault(); action('launch', {url: $('canvas-url').value, output: $('output').value, extension: $('extension').value, browser: $('browser').value, relearn: $('relearn').checked});
});
$('ready').onclick = () => action('ready');
$('retry').onclick = () => action('retry');
$('checked').onclick = () => action('checked', {reviewed: $('reviewed').checked});
$('reviewed').onchange = () => { $('checked').disabled = !$('reviewed').checked; };
$('stop').onclick = () => action('stop');
$('browse').onclick = async () => {
  $('browse').disabled = true; showError('');
  try { const result = await api('/api/browse', {}); if (result.folder) $('output').value = result.folder; }
  catch (error) { showError(error.message); }
  finally { $('browse').disabled = state && !terminal.has(state.phase); }
};
for (const id of ['open-folder', 'open-first']) $(id).onclick = async () => {
  try { await api('/api/' + id, {}); } catch (error) { showError(error.message); }
};
$('quit').onclick = async () => {
  if (state && !terminal.has(state.phase) && !confirm('Stop this collection and quit? Completed files will be preserved.')) return;
  try { await api('/api/quit', {}); window.dispatchEvent(new Event('canvas-tools-closed')); closed = true; $('stage-title').textContent = 'Local app closed'; $('stage-description').textContent = 'You can close this tab. Use Start App to reopen it.'; }
  catch (error) { showError(error.message); }
};
async function poll() {
  if (closed) return;
  try { await refresh(); } catch (_) { showError('The local app is not responding. Check its launcher window or reopen it using Start App.'); }
  setTimeout(poll, 700);
}
poll();
