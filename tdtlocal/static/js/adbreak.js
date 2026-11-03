// While an ad break is on (SCTE-35), do what the user chose: mute or lower the sound, switch channel, show a web.
// What to do with each marker is decided in lib/adbreak.js; this file keeps the one break under way and its timers.
import {
  END_MARGIN, STALE_SECONDS, altChannel, duckedGain, reactionTo, remainingOf, segmentStart, startText, untilMarker,
  waitFor,
} from './lib/adbreak.js';

const WAIT_FOR_SEGMENT_S = 20; // the most to wait for the player to ask for the segment a marker belongs to

/**
 * What the reaction needs from the page.
 * @typedef {object} AdsHost
 * @property {() => import('./tile.js').Tile} focusTile
 * @property {() => object[]} channels
 * @property {() => {v: number, m: boolean}} volume
 * @property {(v: {v: number, m: boolean}) => void} setVolume - set the user's volume (and apply it)
 * @property {(c: object) => void} tune - show a channel in the active tile without remembering it as "the last one"
 * @property {(original: import('./tile.js').Tile, c: object) => ({spare: object, was: object | null} | null)} park - single
 *   view only: show a channel in another tile and leave `original` playing in the background (`null` in multi-view)
 * @property {(parked: object, original: import('./tile.js').Tile, quiet: boolean) => void} unpark - undo `park`
 * @property {(text: string) => void} say - on-screen notice
 */

/** @type {AdsHost | null} */
let host = null;
let cfg = null; // the settings the server sent (`meta.ads`), or null when there is nothing to react with
let current = null; // the break under way
const waiting = new Set(); // markers whose segment the player has not asked for yet
const scheduled = new Set(); // markers with a timer set

/** @param {AdsHost} appHost */
export function initAds(appHost) {
  host = appHost;
}

/** The settings changed (or arrived). `null` switches the reaction off, putting back what it had changed. */
export function setAds(config) {
  cfg = config || null;
  if (!cfg) {
    finish(false);
    for (const item of [...scheduled, ...waiting]) drop(item);
  }
}

export const adsOn = () => !!cfg;

const latencyOf = (tile) => (tile.hls && Number.isFinite(tile.hls.latency) ? Math.max(0, tile.hls.latency) : 0);

function drop(item) {
  clearTimeout(item.timer);
  clearTimeout(item.waitTimer);
  scheduled.delete(item);
  waiting.delete(item);
}

function arm(item, seconds) {
  waiting.delete(item);
  clearTimeout(item.waitTimer);
  item.timer = setTimeout(() => { scheduled.delete(item); item.run(0); }, seconds * 1000);
  scheduled.add(item);
}

/**
 * A marker the player will never reach because it is already behind it: the page was opened, or the channel
 * tuned, in the middle of a break. A break that is still going on is joined; one that is over is not.
 */
function late(item, behind) {
  if (item.kind === 'end') { item.run(0); return; }
  if (item.ev.duration && item.ev.duration - behind >= Math.max(cfg.min_seconds, 1)) item.run(behind);
}

/**
 * Run `item.run` when the player reaches the marker. A marker is seen in the playlist some seconds before it plays,
 * so it is not acted upon when it is found: it waits for the segment it belongs to (or, if the player does not
 * know that segment, for the time the server's measurements say).
 */
function schedule(tile, ev, kind, run) {
  const item = { tile, ev, kind, run, timer: 0, waitTimer: 0 };
  if (ev.kind === 'cue-out-cont' || ev.apply_now) { run(0); return; } // already timed by the server
  const start = segmentStart(tile.fragStart, ev.seg);
  if (start !== undefined) { arm(item, waitFor(start, tile.video.currentTime, tile.video.playbackRate)); return; }
  const ahead = untilMarker(ev, tile.hls ? tile.hls.latency : NaN, Date.now() / 1000);
  if (ahead !== undefined && ahead < -STALE_SECONDS) { late(item, -ahead); return; }
  if (!ev.seg) { arm(item, ahead === undefined ? latencyOf(tile) : Math.max(0, ahead)); return; }
  // by the time the marker is due the player has asked for its segment: until then, the exact position can arrive
  waiting.add(item);
  const patience = ahead === undefined ? WAIT_FOR_SEGMENT_S : Math.min(WAIT_FOR_SEGMENT_S, Math.max(0, ahead) + 2);
  item.waitTimer = setTimeout(() => arm(item, 0), patience * 1000);
}

/** The player asked for a new segment: a marker may have been waiting for exactly this one. */
export function fragSeen(tile) {
  for (const item of [...waiting]) {
    if (item.tile !== tile) continue;
    const start = segmentStart(tile.fragStart, item.ev.seg);
    if (start !== undefined) arm(item, waitFor(start, tile.video.currentTime, tile.video.playbackRate));
  }
}

/** New events from the log, oldest first. Only those of the channel being watched matter. */
export function adsEvents(events) {
  if (!cfg || !host) return;
  for (const ev of events) {
    const tile = host.focusTile();
    const watching = tile && tile.c ? tile.c.id : '';
    // A break already started in-band only borrows the playlist's duration for its safety net: the
    // in-band end (or this cue-in) closes it.
    if (current && current.inband && ev.channel === current.channelId
      && (ev.kind === 'cue-out' || ev.kind === 'cue-out-cont')) {
      clearTimeout(current.timer);
      current.timer = setTimeout(() => finish(false), (remainingOf(ev, cfg).seconds + END_MARGIN) * 1000);
      continue;
    }
    const what = reactionTo(ev, cfg, { channelId: current ? current.channelId : watching, inBreak: !!current });
    if (what === 'start' && watching) schedule(tile, ev, 'start', (behind) => begin(tile, ev, behind));
    else if (what === 'end') schedule(current.tile, ev, 'end', () => finish(false));
  }
}

/**
 * An in-band SCTE-35 cue is due: the sample has reached the picture, so apply it now. `action` is 1
 * for a break start and -1 for its end. It goes through the same reaction as a playlist marker.
 */
export function adsInband(tile, action) {
  if (!cfg || !host || !tile || !tile.c) return;
  if (action > 0) {
    if (!current) begin(tile, { channel: tile.c.id, kind: 'cue-out', duration: 0, inband: true }, 0);
  } else if (action < 0 && current && current.tile === tile) {
    finish(false);
  }
}

function begin(tile, ev, behind) {
  if (!cfg || current || tile !== host.focusTile() || !tile.c || tile.c.id !== ev.channel) return;
  const channel = tile.c;
  const b = {
    tile, channel, channelId: channel.id, before: { ...host.volume() }, audioTouched: false, tuned: false, timer: 0,
    park: null, inband: !!ev.inband,
  };
  current = b;
  if (cfg.flag) tile.setAdFlag(true);
  if (cfg.audio === 'mute') tile.setDuck({ muted: true });
  else if (cfg.audio === 'lower') tile.setDuck({ gain: duckedGain(cfg.lower_percent) });
  let altName = '';
  if (cfg.screen === 'channel') {
    const alt = altChannel(host.channels(), cfg.channel);
    if (alt && alt.id !== channel.id) {
      altName = alt.name;
      // single view: the original keeps playing in the background, so it still tells when the break is over and does
      // not have to be loaded again (that would start with another ad); multi-view: the tile itself is switched
      b.park = host.park(tile, alt);
      if (!b.park) host.tune(alt);
    }
  } else if (cfg.screen === 'web') {
    tile.showAdFrame(cfg.web_url);
  }
  // when the original cannot be watched any more, nothing tells that the break is over: the assumed length rules
  const left = Math.max(0, remainingOf(ev, cfg).seconds - behind);
  b.timer = setTimeout(() => finish(false), (left + END_MARGIN) * 1000);
  host.say(startText(cfg, altName));
}

/**
 * Put things back. `quiet`: the user took over (a channel change they made), so only what the reaction itself set is
 * lifted and nobody is taken back to the original channel.
 */
function finish(quiet) {
  const b = current;
  if (!b) return;
  current = null;
  clearTimeout(b.timer);
  for (const item of [...scheduled]) if (item.kind === 'end') drop(item);
  b.tile.setDuck(null);
  b.tile.hideAdFrame();
  b.tile.setAdFlag(false);
  if (b.park) host.unpark(b.park, b.tile, quiet);
  if (quiet) return;
  if (b.audioTouched && cfg && cfg.on_touch === 'restore') host.setVolume(b.before);
  const tile = b.tile;
  if (!b.park && tile === host.focusTile() && tile.c && tile.c.id !== b.channelId) host.tune(b.channel);
  host.say('Fin de la pausa de anuncios');
}

/** The user changed the volume of the active tile while the reaction held its own: their value wins, now. */
export function adsUserVolume(tile, value) {
  tile.setDuck(null);
  host.setVolume(value);
  if (current && current.tile === tile) current.audioTouched = true;
}

/** The user chose a channel (a change the reaction did not make). */
export function adsTuned() {
  if (!current) return;
  current.tuned = true;
  current.tile.setDuck(null);
  current.tile.hideAdFrame();
  if (!cfg || cfg.on_touch === 'respect') finish(true); // what they chose stays: nothing is undone
}
