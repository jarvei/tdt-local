// The settings dialog ("Ajustes"): assistant, live subtitles (Whisper, with model download) and lists/guide.
// What is shown is described in lib/settings-form.js; everything is validated and saved by the server.
import { requestJson } from './api.js';
import { $, el } from './dom.js';
import { LOGS_INTRO, MODES, filterLines, summarize as summarizeLogs } from './lib/logs-panel.js';
import { PLUGINS_INTRO, describePlugin, summarize } from './lib/plugins-panel.js';
import {
  FIELDS, TABS, collectChanges, describeDiagnosis, describeDownload, fieldsOf, formatBytes, percent,
} from './lib/settings-form.js';

const POLL_MS = 700;
const inputs = new Map(); // setting name -> its input element
let snapshot = null; // last /api/settings answer
let hooks = null;
let pollTimer = null;
let removeKey = null; // the "remove saved key" checkbox

// only the desktop app has window.tdtDesktop; in a browser the settings have no «Plugins» tab
const desktop = () => (typeof window !== 'undefined' && window.tdtDesktop && window.tdtDesktop.plugins) || null;
const desktopLogs = () => (typeof window !== 'undefined' && window.tdtDesktop && window.tdtDesktop.logs) || null;
const tabsShown = () => (desktop()
  ? [...TABS, { id: 'plugins', label: 'Plugins' }, ...(desktopLogs() ? [{ id: 'logs', label: 'Registro' }] : [])]
  : TABS);

const dlg = () => $('setDlg');
const say = (text, bad = false) => {
  const n = $('setMsg');
  n.textContent = text;
  n.classList.toggle('ok', !!text && !bad);
};

/* ------------------------------------------------------------ building the form */
function fieldNode(f) {
  const wrap = el('div', 'field');
  let input;
  if (f.type === 'bool') {
    input = el('input');
    input.type = 'checkbox';
    const label = el('label', 'chk');
    label.append(input, document.createTextNode(' ' + f.label));
    wrap.append(label);
  } else {
    const label = el('label');
    label.append(el('span', '', f.label));
    if (f.type === 'select') {
      input = el('select');
      for (const [value, text] of Object.entries(f.options)) input.append(new Option(text, value));
    } else if (f.type === 'json') {
      input = el('textarea');
      input.rows = 3;
      input.spellcheck = false;
    } else {
      input = el('input');
      input.type = f.type === 'number' ? 'number' : f.type === 'secret' ? 'password' : 'text';
      if (f.type === 'number') input.step = String(f.step || 1);
      if (f.type === 'secret') input.autocomplete = 'new-password';
      if (f.list) input.setAttribute('list', f.list);
      input.spellcheck = false;
    }
    label.append(input);
    wrap.append(label);
  }
  input.id = 'set_' + f.name;
  inputs.set(f.name, input);
  if (f.hint) wrap.append(el('p', 'dlg-hint', f.hint));
  if (f.type === 'secret') {
    const chk = el('label', 'chk');
    removeKey = el('input');
    removeKey.type = 'checkbox';
    chk.append(removeKey, document.createTextNode(' Quitar la clave guardada'));
    chk.hidden = true;
    wrap.append(chk);
    wrap.secretRow = chk;
  }
  wrap.append(el('p', 'field-error'));
  if (f.test) wrap.append(testBlock(f.test));
  return wrap;
}

function testBlock(kind) {
  const box = el('div', 'test');
  const button = el('button', 'btn', kind === 'ai' ? 'Probar conexión y listar modelos' : 'Comprobar');
  button.type = 'button';
  const result = el('div', 'test-result');
  result.setAttribute('role', 'status');
  const test = { ai: testAssistant, web: testWeb, stt: testWhisper }[kind];
  button.addEventListener('click', () => test(button, result));
  box.append(button, result);
  return box;
}

function modelsBlock() {
  const box = el('div', 'models');
  box.append(el('h4', '', 'Modelos de Whisper'));
  const installed = el('ul', 'models-installed');
  installed.id = 'modelsInstalled';
  const pick = el('label');
  pick.append(el('span', '', 'Descargar un modelo'));
  const select = el('select');
  select.id = 'modelCatalog';
  pick.append(select);
  const row = el('div', 'models-actions');
  const go = el('button', 'btn', 'Descargar');
  go.type = 'button';
  go.id = 'modelGo';
  const cancel = el('button', 'btn', 'Cancelar');
  cancel.type = 'button';
  cancel.id = 'modelCancel';
  cancel.hidden = true;
  row.append(go, cancel);
  const progress = el('progress');
  progress.id = 'modelProgress';
  progress.max = 100;
  progress.hidden = true;
  const status = el('p', 'dlg-hint');
  status.id = 'modelStatus';
  status.setAttribute('role', 'status');
  const where = el('p', 'dlg-hint');
  where.id = 'modelsWhere';
  box.append(installed, pick, row, progress, status, where);
  go.addEventListener('click', startDownload);
  cancel.addEventListener('click', () => requestJson('/api/whisper/download/cancel', 'POST', {}));
  return box;
}

function helpBlock() {
  const p = el('p', 'dlg-hint');
  p.append('Whisper necesita ', link('https://ffmpeg.org', 'ffmpeg'), ' en el PATH y el programa ',
    link('https://github.com/ggml-org/whisper.cpp', 'whisper.cpp'),
    ' (whisper-server). El audio se transcribe en tu equipo y no sale de él.');
  return p;
}

function scteHelp() {
  const box = el('div', 'scte-help');
  box.append(el('p', 'dlg-hint', 'SCTE-35 es la señal con la que una cadena marca dónde empieza y acaba la publicidad. '
    + 'TDT local la busca en las listas de reproducción (y, si lo activas, dentro del vídeo) y guarda cada hallazgo en un '
    + 'registro local, scte.jsonl, en la carpeta de datos. No salta ni bloquea nada: solo avisa y guarda.'));
  const open = el('button', 'btn', 'Ver el registro de marcas…');
  open.type = 'button';
  open.addEventListener('click', () => { dlg().close(); hooks.openScte(); });
  box.append(open);
  return box;
}

function adsHelp() {
  return el('p', 'dlg-hint', 'Lo que hace la página mientras el canal que ves está en una pausa de anuncios. Necesita '
    + 'que el canal envíe la marca en su playlist (el servidor la sigue cada segundo; muchos canales no la envían). '
    + 'Nada se bloquea ni se salta: el vídeo sigue su curso.');
}

/* ------------------------------------------------------------ plugins (desktop app only) */
function pluginsPanel() {
  const box = el('div', 'plugins');
  box.append(el('p', 'dlg-hint', PLUGINS_INTRO));
  const summary = el('p', 'dlg-hint');
  summary.id = 'pluginSummary';
  summary.setAttribute('role', 'status');
  const list = el('ul', 'plugin-list');
  list.id = 'pluginList';
  const apply = el('p', 'dlg-hint plugin-apply');
  apply.id = 'pluginApply';
  apply.hidden = true;
  const reload = el('button', 'btn', 'Recargar ahora');
  reload.type = 'button';
  reload.addEventListener('click', () => window.tdtDesktop.reload());
  apply.append('Los cambios se aplican al recargar la ventana. ', reload);
  const actions = el('div', 'plugin-actions');
  const open = el('button', 'btn', 'Abrir la carpeta de plugins');
  open.type = 'button';
  open.addEventListener('click', () => desktop().openFolder());
  const rescan = el('button', 'btn', 'Buscar de nuevo');
  rescan.type = 'button';
  rescan.addEventListener('click', async () => paintPlugins(await desktop().rescan()));
  actions.append(open, rescan);
  const where = el('p', 'dlg-hint');
  where.id = 'pluginFolder';
  box.append(summary, list, apply, actions, where);
  return box;
}

function paintPlugins(data) {
  if (!data) return;
  const list = $('pluginList');
  list.replaceChildren();
  for (const plugin of data.plugins) {
    const d = describePlugin(plugin);
    const item = el('li', 'plugin' + (d.usable ? '' : ' broken'));
    const label = el('label', 'chk');
    const box = el('input');
    box.type = 'checkbox';
    box.checked = d.on;
    box.disabled = !d.usable;
    box.addEventListener('change', async () => {
      paintPlugins(await desktop().setEnabled(plugin.id, box.checked));
      $('pluginApply').hidden = false;
    });
    label.append(box, el('strong', '', ' ' + d.title));
    item.append(label);
    for (const line of [d.description, d.acts, d.source]) if (line) item.append(el('p', 'dlg-hint', line));
    for (const problem of d.problems) item.append(el('p', 'field-error', 'No se puede usar: ' + problem));
    list.append(item);
  }
  $('pluginSummary').textContent = summarize(data.plugins);
  $('pluginFolder').textContent = 'Los tuyos van en una carpeta por plugin dentro de: ' + data.folder;
}

/* ------------------------------------------------------------ log (desktop app only) */
let logData = null; // what the app last said: {lines, folder}

function logsPanel() {
  const box = el('div', 'logs');
  box.append(el('p', 'dlg-hint', LOGS_INTRO));
  const bar = el('div', 'plugin-actions');
  const mode = el('select');
  mode.id = 'logMode';
  mode.setAttribute('aria-label', 'Qué líneas ver');
  for (const [value, text] of Object.entries(MODES)) mode.append(new Option(text, value));
  mode.addEventListener('change', paintLogs);
  const button = (text, fn) => {
    const b = el('button', 'btn', text);
    b.type = 'button';
    b.addEventListener('click', fn);
    return b;
  };
  bar.append(mode,
    button('Actualizar', loadLogs),
    button('Copiar', copyLogs),
    button('Guardar en un archivo…', saveLogs),
    button('Abrir la carpeta de registros', () => desktopLogs().openFolder()));
  const summary = el('p', 'dlg-hint');
  summary.id = 'logSummary';
  summary.setAttribute('role', 'status');
  const view = el('pre', 'log-view');
  view.id = 'logView';
  view.tabIndex = 0;
  view.setAttribute('aria-label', 'Registro de la aplicación');
  const msg = el('p', 'dlg-hint');
  msg.id = 'logMsg';
  msg.setAttribute('role', 'status');
  box.append(bar, summary, view, msg);
  return box;
}

function paintLogs() {
  if (!logData) return;
  const lines = filterLines(logData.lines, $('logMode').value);
  const view = $('logView');
  view.textContent = lines.length ? lines.join('\n') : 'No hay líneas con este filtro.';
  view.scrollTop = view.scrollHeight;
  $('logSummary').textContent = summarizeLogs(logData.lines) + (lines.length < logData.lines.length ? ' · se ven ' + lines.length : '')
    + '. Archivo: ' + logData.folder;
}

async function loadLogs() {
  if (!desktopLogs()) return;
  logData = await desktopLogs().get();
  paintLogs();
}

async function copyLogs() {
  if (!logData) await loadLogs();
  const text = filterLines(logData.lines, $('logMode').value, Infinity).join('\n');
  try {
    await navigator.clipboard.writeText(text);
    $('logMsg').textContent = 'Copiado (' + text.split('\n').length + ' líneas).';
  } catch {
    $('logMsg').textContent = 'El navegador no deja copiar: usa «Guardar en un archivo…».';
  }
}

async function saveLogs() {
  const res = await desktopLogs().save();
  $('logMsg').textContent = res && res.saved ? 'Guardado en ' + res.saved : 'No se ha guardado nada.';
}

async function loadPlugins() {
  if (desktop()) paintPlugins(await desktop().list());
}

function link(href, text) {
  const a = el('a', '', text);
  a.href = href;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  return a;
}

function build() {
  const nav = $('setTabs');
  const panels = $('setPanels');
  for (const tab of tabsShown()) {
    const button = el('button', '', tab.label);
    button.type = 'button';
    button.id = 'tab_' + tab.id;
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-controls', 'panel_' + tab.id);
    button.addEventListener('click', () => showTab(tab.id));
    nav.append(button);

    const panel = el('section');
    panel.id = 'panel_' + tab.id;
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', button.id);
    if (tab.id === 'plugins' || tab.id === 'logs') { // not server settings: they talk to the app
      panel.append(tab.id === 'plugins' ? pluginsPanel() : logsPanel());
      panels.append(panel);
      continue;
    }
    const advanced = el('details', 'adv');
    advanced.append(el('summary', '', 'Fuentes de datos (avanzado)'));
    for (const f of fieldsOf(tab.id)) {
      (f.advanced ? advanced : panel).append(fieldNode(f));
      if (f.name === 'whisper_model') panel.append(modelsBlock());
    }
    if (tab.id === 'stt') panel.prepend(helpBlock());
    if (tab.id === 'scte') panel.prepend(scteHelp());
    if (tab.id === 'ads') panel.prepend(adsHelp());
    if (advanced.children.length > 1) panel.append(advanced);
    const reset = el('button', 'btn reset', 'Restaurar valores de esta pestaña');
    reset.type = 'button';
    reset.addEventListener('click', () => resetTab(tab.id));
    panel.append(reset);
    panels.append(panel);
  }
  nav.addEventListener('keydown', (e) => {
    const ids = tabsShown().map((t) => t.id);
    const i = ids.findIndex((id) => $('tab_' + id).getAttribute('aria-selected') === 'true');
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      const next = ids[(i + (e.key === 'ArrowRight' ? 1 : -1) + ids.length) % ids.length];
      showTab(next);
      $('tab_' + next).focus();
    }
  });
  for (const id of ['llmModels', 'whisperModels']) {
    const list = el('datalist');
    list.id = id;
    dlg().append(list);
  }
}

function showTab(id) {
  for (const tab of tabsShown()) {
    const on = tab.id === id;
    $('tab_' + tab.id).setAttribute('aria-selected', on ? 'true' : 'false');
    $('tab_' + tab.id).tabIndex = on ? 0 : -1;
    $('panel_' + tab.id).hidden = !on;
  }
}

/* ------------------------------------------------------------ values <-> form */
function fill(snap) {
  snapshot = snap;
  for (const f of FIELDS) {
    const input = inputs.get(f.name);
    const value = snap.values[f.name];
    if (f.type === 'bool') input.checked = !!value;
    else input.value = value === undefined || value === null ? '' : String(value);
    const locked = snap.locked.includes(f.name);
    input.disabled = locked;
    const wrap = input.closest('.field');
    wrap.classList.toggle('locked', locked);
    wrap.querySelector('.field-error').textContent = locked
      ? 'Fijado por la línea de comandos o la variable TDT_' + f.name.toUpperCase() + '; se ignora lo guardado.' : '';
    if (f.type === 'secret') {
      input.value = '';
      input.placeholder = snap.secrets_set[f.name] ? '•••••••• (guardada)' : '';
      wrap.secretRow.hidden = !snap.secrets_set[f.name] || locked;
      removeKey.checked = false;
    }
  }
  $('modelsWhere').textContent = '';
}

function readForm() {
  const out = {};
  for (const f of FIELDS) {
    const input = inputs.get(f.name);
    if (input.disabled) continue;
    if (f.type === 'bool') out[f.name] = input.checked;
    else if (f.type === 'number') out[f.name] = input.value.trim() === '' ? input.value : Number(input.value);
    else if (f.type !== 'secret') out[f.name] = input.value;
  }
  return out;
}

function showErrors(errors) {
  for (const f of FIELDS) {
    const note = inputs.get(f.name).closest('.field').querySelector('.field-error');
    if (errors[f.name]) note.textContent = errors[f.name];
    else if (!inputs.get(f.name).disabled) note.textContent = '';
  }
  const first = FIELDS.find((f) => errors[f.name]);
  if (first) {
    showTab(first.tab);
    inputs.get(first.name).focus();
  }
}

/* ------------------------------------------------------------ actions */
async function save(e) {
  e.preventDefault();
  const typed = inputs.get('llm_api_key');
  const changes = collectChanges(snapshot.values, readForm(), { secret: typed.value, removeSecret: removeKey.checked });
  if (!Object.keys(changes).length) { say('No hay cambios que guardar.'); return; }
  say('Guardando…');
  let res;
  try { res = await requestJson('/api/settings', 'POST', changes); } catch { say('El servidor local no responde.', true); return; }
  if (!res.ok) {
    showErrors((res.data && res.data.errors) || {});
    say('Revisa los campos marcados: no se ha guardado nada.', true);
    return;
  }
  fill(res.data.settings);
  say('Guardado. Los cambios ya están en uso.');
  loadModels();
  await hooks.reload();
}

async function resetTab(tab) {
  if (!window.confirm('¿Restaurar los valores por defecto de esta pestaña?')) return;
  const res = await requestJson('/api/settings/reset', 'POST', { section: tab });
  if (res.ok) {
    fill(res.data.settings);
    say(res.data.changed.length ? 'Valores restaurados.' : 'Ya tenía los valores por defecto.');
    await hooks.reload();
  } else say('No se pudo restaurar.', true);
}

async function testAssistant(button, out) {
  button.disabled = true;
  out.textContent = 'Conectando…';
  out.className = 'test-result';
  const typed = inputs.get('llm_api_key').value;
  let res;
  try {
    res = await requestJson('/api/settings/test/ai', 'POST', { url: inputs.get('llm_url').value, api_key: typed || null });
  } catch { res = { data: { ok: false, error: 'El servidor local no responde.' } }; }
  button.disabled = false;
  const d = res.data || { ok: false, error: 'Respuesta no válida.' };
  out.classList.add(d.ok ? 'good' : 'bad');
  if (!d.ok) { out.textContent = '✖ ' + d.error; return; }
  out.textContent = d.models.length
    ? '✔ Conectado. Modelos disponibles: ' + d.models.join(', ')
    : '✔ Conectado, pero el servidor no lista modelos.';
  $('llmModels').replaceChildren(...d.models.map((m) => new Option(m, m)));
}

async function testWeb(button, out) {
  button.disabled = true;
  out.textContent = 'Comprobando…';
  out.className = 'test-result';
  let res;
  try {
    res = await requestJson('/api/settings/test/web', 'POST', { url: inputs.get('ad_web_url').value });
  } catch { res = { data: { ok: false, error: 'El servidor local no responde.' } }; }
  button.disabled = false;
  const d = res.data || { ok: false, error: 'Respuesta no válida.' };
  out.classList.add(d.ok ? 'good' : 'bad');
  out.textContent = d.ok ? '✔ La web responde y permite mostrarse dentro de la página.' : '✖ ' + d.error;
}

async function testWhisper(button, out) {
  button.disabled = true;
  out.textContent = 'Comprobando…';
  out.className = 'test-result';
  let res;
  try {
    res = await requestJson('/api/settings/test/stt', 'POST', {
      whisper_bin: inputs.get('whisper_bin').value, whisper_model: inputs.get('whisper_model').value,
      whisper_url: inputs.get('whisper_url').value,
    });
  } catch { res = { data: { ok: false, error: 'El servidor local no responde.' } }; }
  button.disabled = false;
  const d = res.data || { ok: false, error: 'Respuesta no válida.' };
  if (!d.ok) { out.classList.add('bad'); out.textContent = '✖ ' + d.error; return; }
  out.replaceChildren(...describeDiagnosis(d).map((r) => el('div', r.ok ? 'good' : 'bad', (r.ok ? '✔ ' : '✖ ') + r.text)));
}

/* ------------------------------------------------------------ Whisper models */
async function loadModels() {
  const res = await requestJson('/api/whisper/models');
  if (!res.ok) return;
  const { installed, catalog, models_dir: dir, download } = res.data;
  $('whisperModels').replaceChildren(...installed.map((m) => new Option(m.file, m.path)));
  const list = $('modelsInstalled');
  list.replaceChildren(...(installed.length ? installed.map((m) => {
    const li = el('li');
    const use = el('button', 'btn', 'Usar');
    use.type = 'button';
    use.addEventListener('click', () => { inputs.get('whisper_model').value = m.path; say('Modelo elegido: pulsa Guardar para usarlo.'); });
    li.append(el('span', '', m.file + ' · ' + formatBytes(m.bytes)), use);
    return li;
  }) : [el('li', 'dlg-hint', 'No hay modelos instalados todavía.')]));
  $('modelCatalog').replaceChildren(...catalog.map((m) => {
    const opt = new Option(`${m.name} · ${m.label} · ≈ ${m.mb >= 1000 ? (m.mb / 1000).toFixed(1).replace('.', ',') + ' GB' : m.mb + ' MB'}`
      + (m.installed ? ' · ya descargado' : '') + ' — ' + m.note, m.name);
    return opt;
  }));
  $('modelsWhere').textContent = 'Carpeta de modelos: ' + dir;
  paintDownload(download);
  if (download.state === 'running') pollDownload();
}

function paintDownload(s) {
  const running = s.state === 'running';
  $('modelGo').disabled = running;
  $('modelCancel').hidden = !running;
  $('modelProgress').hidden = !running;
  if (running) $('modelProgress').value = percent(s.done, s.total);
  $('modelStatus').textContent = describeDownload(s);
}

function pollDownload() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    const res = await requestJson('/api/whisper/download').catch(() => null);
    if (!res || !res.ok) return;
    paintDownload(res.data);
    if (res.data.state === 'running') { pollDownload(); return; }
    await loadModels();
    if (res.data.state === 'done') {
      inputs.get('whisper_model').value = res.data.path;
      say('Modelo descargado y elegido: pulsa Guardar para usarlo.');
    }
  }, POLL_MS);
}

async function startDownload() {
  const res = await requestJson('/api/whisper/download', 'POST', { name: $('modelCatalog').value });
  if (res.data && res.data.download) paintDownload(res.data.download);
  if (res.ok) pollDownload();
  else say((res.data && res.data.error) || 'Ya hay una descarga en curso.', true);
}

/* ------------------------------------------------------------ opening */
async function open() {
  say('');
  const res = await requestJson('/api/settings').catch(() => null);
  if (!res || !res.ok) { hooks.footMsg('No se pudieron leer los ajustes.'); return; }
  fill(res.data);
  showTab('ai');
  dlg().showModal();
  loadModels();
  loadPlugins();
  loadLogs();
}

/**
 * @param {{reload: () => Promise<void>, footMsg: (t: string) => void, openScte: () => void}} appHooks
 */
export function initSettings(appHooks) {
  hooks = appHooks;
  build();
  $('btnSettings').addEventListener('click', open);
  $('setForm').addEventListener('submit', save);
  $('setClose').addEventListener('click', () => dlg().close());
  dlg().addEventListener('close', () => clearTimeout(pollTimer));
}
