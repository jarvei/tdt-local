// Reacting to an ad break (SCTE-35): the decisions, as pure functions. adbreak.js is the one that acts on them.
import { findChannel } from './commands.js';

/** Seconds after the announced end of a break before giving up waiting for the "break is over" marker. */
export const END_MARGIN = 3;

const BREAK_STARTS = ['cue-out', 'cue-out-cont'];

function safeDecode(text) {
  try { return decodeURIComponent(text); } catch { return text; }
}

/** The last part of a URL's path, without query: how the server names the segment a marker belongs to. */
const lastPart = (url) => String(url).split(/[?#]/)[0].replace(/\/+$/, '').split('/').pop();

/**
 * Where, on the video timeline, the segment called `seg` starts. `undefined` if the player has not seen it (yet).
 * @param {Map<string, number>} starts - segment URL -> start in seconds (the newest are last)
 * @param {string | undefined} seg
 */
export function segmentStart(starts, seg) {
  if (!seg) return undefined;
  let found;
  for (const [url, start] of starts) {
    const name = lastPart(url);
    if (name === seg || safeDecode(name) === safeDecode(seg)) found = start;
  }
  return found;
}

/** A marker this much behind the player (seconds) is something that already played, not something about to. */
export const STALE_SECONDS = 1.5;

/**
 * Seconds until the player reaches a marker, estimated when the segment it belongs to is not known (yet): where it
 * was, relative to the end of the playlist, when the server saw it, against how far behind that end the player is.
 * Negative when it has already gone by. `undefined` when there is not enough to tell.
 * @param {{lead?: number, ts?: number}} ev
 * @param {number} latency - seconds the player is behind the live edge
 * @param {number} nowSeconds
 */
export function untilMarker(ev, latency, nowSeconds) {
  if (ev.lead === undefined || ev.lead === null || !Number.isFinite(Number(ev.lead)) || !Number.isFinite(latency)) return undefined;
  const age = Math.min(15, Math.max(0, nowSeconds - Number(ev.ts || nowSeconds)));
  return latency - Number(ev.lead) - age;
}

/** Seconds to wait until the position `start` is reached. Never negative; a faster playback gets there sooner. */
export const waitFor = (start, now, rate = 1) => Math.max(0, (start - now) / (rate > 0 ? rate : 1));

/**
 * How much of the break is left, from what the marker says. `known` is false when it does not say.
 * @returns {{known: boolean, seconds: number}}
 */
export function remainingOf(ev, cfg) {
  const total = Number(ev.duration) > 0 ? Number(ev.duration) : 0;
  if (!total) return { known: false, seconds: cfg.unknown_seconds };
  const elapsed = Number(ev.elapsed) > 0 ? Number(ev.elapsed) : 0;
  return { known: true, seconds: Math.max(0, total - elapsed) };
}

/** A break that says it is shorter than the user wants to bother with. */
export function tooShort(ev, cfg) {
  const left = remainingOf(ev, cfg);
  return left.known && left.seconds < cfg.min_seconds;
}

/**
 * What an event means for the channel being watched: `'start'` a reaction, `'end'` the one under way, or nothing.
 * @param {{kind: string, channel: string}} ev
 * @param {{channelId: string, inBreak: boolean}} where - the channel watched (or the one the break began on)
 */
export function reactionTo(ev, cfg, { channelId, inBreak }) {
  if (!cfg || ev.channel !== channelId) return null;
  if (ev.kind === 'cue-in') return inBreak ? 'end' : null;
  if (BREAK_STARTS.includes(ev.kind)) return !inBreak && !tooShort(ev, cfg) ? 'start' : null;
  return null;
}

/** Volume factor for "lower the volume to N %". */
export const duckedGain = (percent) => Math.min(1, Math.max(0, Number(percent) / 100));

/**
 * The audio a player should have: the user's volume, changed by the reaction under way (if any).
 * @param {{v: number, m: boolean}} vol - what the user has chosen
 * @param {{gain?: number, muted?: boolean} | null} duck
 * @param {boolean} hasSound - this player is the one that owns the audio
 */
export function audioTarget(vol, duck, hasSound) {
  const gain = duck && duck.gain !== undefined ? duck.gain : 1;
  return { v: vol.v * gain, m: !hasSound || !!vol.m || !!(duck && duck.muted) };
}

/** The player's audio is not what the reaction set: someone (the user) changed it. */
export const audioTouched = (actual, wanted) => actual.m !== wanted.m || Math.abs(actual.v - wanted.v) > 0.011;

/**
 * The channel to switch to: by id, or by name as the voice commands understand it (no case, accents or article).
 * @param {Array<{id: string, name: string, kind: string, sources: unknown[], ok: boolean | null}>} channels
 */
export function altChannel(channels, wanted) {
  const text = String(wanted || '').trim();
  if (!text) return undefined;
  return channels.find((c) => c.id === text && c.sources.length) || findChannel(channels, 'tv', text) || undefined;
}

/** What the on-screen notice says when a reaction starts. */
export function startText(cfg, altName) {
  const parts = [];
  if (cfg.audio === 'mute') parts.push('sonido silenciado');
  if (cfg.audio === 'lower') parts.push('volumen al ' + cfg.lower_percent + ' %');
  if (altName) parts.push('viendo ' + altName);
  if (cfg.screen === 'web' && !altName) parts.push('mostrando una web');
  return 'Pausa de anuncios' + (parts.length ? ' · ' + parts.join(' · ') : '');
}
