// The "Sonido" dialog: volume leveler and equalizer, for every channel or only for the active one.
// The settings and their rules are in lib/audio.js; the Web Audio chain that applies them is in audio.js.
import { audioSupported, awake } from './audio.js';
import { $, el } from './dom.js';
import {
  BANDS, EQ_RANGE, PRESETS, effective, formatDb, presetOf, sanitize, withChannel, withGlobal,
} from './lib/audio.js';
import { focusTile } from './state.js';
import { store } from './store.js';

const READ_MS = 400;
let prefs = sanitize(store.get('sound', null));
let hooks = null;
let timer = null;
let busy = false; // filling the controls is not the user changing them
let bandRows = [];

const dlg = () => $('soundDlg');
const activeChannel = () => focusTile().c;
const scope = () => ($('soundChannel').checked && activeChannel() ? 'channel' : 'global');
const hz = (f) => (f >= 1000 ? f / 1000 + ' kHz' : f + ' Hz');

/** What applies to a channel (the page asks it whenever it sets up a tile's sound). */
export function audioFx(c) {
  const e = effective(prefs, c ? c.id : undefined);
  return { level: e.level, eq: e.eq };
}

const shown = () => effective(prefs, scope() === 'channel' ? activeChannel().id : undefined);

function buildBands() {
  const box = $('eqBands');
  bandRows = BANDS.map((freq) => {
    const row = el('label', 'eq-row');
    const input = el('input');
    input.type = 'range';
    input.min = String(-EQ_RANGE);
    input.max = String(EQ_RANGE);
    input.step = '1';
    input.setAttribute('aria-label', hz(freq));
    const out = el('output');
    row.append(el('span', '', hz(freq)), input, out);
    input.addEventListener('input', () => commit('eq'));
    box.append(row);
    return { input, out };
  });
  $('eqPreset').replaceChildren(...Object.entries(PRESETS).map(([id, p]) => new Option(p.label, id)), new Option('Personalizado', 'custom'));
}

const read = {
  level: () => ({ on: $('levelOn').checked, target: Number($('levelTarget').value), boost: Number($('levelBoost').value) }),
  eq: () => ({ on: $('eqOn').checked, gains: bandRows.map((b) => Number(b.input.value)) }),
};

/** The controls take the values of what is shown (everything, or only the labels while the user is dragging). */
function paint(all) {
  busy = true;
  const e = shown();
  if (all) {
    $('levelOn').checked = e.level.on;
    $('levelTarget').value = String(e.level.target);
    $('levelBoost').value = String(e.level.boost);
    $('eqOn').checked = e.eq.on;
    bandRows.forEach((b, i) => { b.input.value = String(e.eq.gains[i]); });
  }
  $('levelTargetOut').textContent = e.level.target + ' (tipo LUFS)';
  $('levelBoostOut').textContent = '+' + e.level.boost + ' dB';
  bandRows.forEach((b, i) => { b.out.textContent = formatDb(e.eq.gains[i]); });
  $('eqPreset').value = presetOf(e.eq.gains) || 'custom';
  $('levelTarget').closest('.snd-sec').classList.toggle('off', !e.level.on);
  $('eqBands').closest('.snd-sec').classList.toggle('off', !e.eq.on);
  own(e);
  busy = false;
}

function own(e) {
  const channel = scope() === 'channel';
  const withOwn = Object.keys(prefs.channels).length;
  $('levelUseGlobal').hidden = !(channel && e.ownLevel);
  $('eqUseGlobal').hidden = !(channel && e.ownEq);
  const note = (mine, what) => (channel
    ? (mine ? 'Este canal tiene su propio ajuste de ' + what + '.' : 'Este canal usa el ajuste de todos los canales; si cambias algo aquí, tendrá el suyo.')
    : '');
  $('levelOwn').textContent = note(e.ownLevel, 'volumen');
  $('eqOwn').textContent = channel ? note(e.ownEq, 'ecualizador')
    : (withOwn ? withOwn + (withOwn === 1 ? ' canal tiene' : ' canales tienen') + ' ajustes propios, que mandan sobre estos.' : '');
}

function commit(part) {
  if (busy) return;
  const value = read[part]();
  const c = activeChannel();
  prefs = scope() === 'channel' ? withChannel(prefs, c.id, part, value) : withGlobal(prefs, part, value);
  store.set('sound', prefs);
  hooks.apply();
  paint(false);
}

function useGlobal(part) {
  prefs = withChannel(prefs, activeChannel().id, part, null);
  store.set('sound', prefs);
  hooks.apply();
  paint(true);
}

function readout() {
  const tile = focusTile();
  const fx = audioFx(tile.c);
  let text;
  if (!audioSupported()) text = 'Este navegador no permite procesar el audio.';
  else if (!tile.c) text = '';
  else if (tile.frame) text = 'El sonido de un reproductor incrustado (Atresplayer o YouTube) lo controla él mismo.';
  else if (!fx.level.on && !fx.eq.on) text = 'Con todo desactivado, el sonido pasa tal cual.';
  else if (!awake()) text = 'El navegador aún no deja procesar el audio: haz clic en cualquier sitio de la página.';
  else if (!tile.chain) text = 'Preparando el procesado…';
  else {
    const r = tile.chain.readout();
    text = Number.isFinite(r.loudness)
      ? 'Ahora mismo: «' + tile.c.name + '» llega a ' + Math.round(r.loudness) + ' y el ajuste es ' + formatDb(r.gainDb) + '.'
      : 'No hay sonido que medir ahora mismo.';
  }
  $('levelRead').textContent = text;
}

export function openSound() {
  const c = activeChannel();
  $('soundChannel').disabled = !c;
  $('soundChannelName').textContent = c ? 'Solo «' + c.name + '»' : 'Solo este canal (elige uno primero)';
  const mine = c ? prefs.channels[c.id] : null;
  $('soundChannel').checked = !!mine;
  $('soundGlobal').checked = !mine;
  $('soundNote').textContent = 'Lo que cambies se guarda en este navegador y se aplica al instante.';
  paint(true);
  readout();
  clearInterval(timer);
  timer = setInterval(readout, READ_MS);
  if (!dlg().open) dlg().showModal();
}

/** @param {{apply: () => void}} appHooks - `apply` makes every tile take the current settings */
export function initSound(appHooks) {
  hooks = appHooks;
  if (!audioSupported()) { $('btnSound').hidden = true; return; }
  buildBands();
  $('btnSound').addEventListener('click', openSound);
  $('soundClose').addEventListener('click', () => dlg().close());
  dlg().addEventListener('close', () => clearInterval(timer));
  for (const name of ['soundGlobal', 'soundChannel']) $(name).addEventListener('change', () => paint(true));
  for (const id of ['levelOn', 'levelTarget', 'levelBoost']) $(id).addEventListener('input', () => commit('level'));
  $('eqOn').addEventListener('input', () => commit('eq'));
  $('eqPreset').addEventListener('change', () => {
    const preset = PRESETS[$('eqPreset').value];
    if (!preset) return;
    busy = true;
    bandRows.forEach((b, i) => { b.input.value = String(preset.gains[i]); });
    $('eqOn').checked = true;
    busy = false;
    commit('eq');
  });
  $('levelUseGlobal').addEventListener('click', () => useGlobal('level'));
  $('eqUseGlobal').addEventListener('click', () => useGlobal('eq'));
}
