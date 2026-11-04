import assert from 'node:assert/strict';
import { test } from 'node:test';

import { matchingWords, parseAlertWords } from '../../tdtlocal/static/js/lib/alerts.js';
import { findChannel, nearestSecs, quickCommand } from '../../tdtlocal/static/js/lib/commands.js';
import { epgDetail, epgLine, epgOf, progress } from '../../tdtlocal/static/js/lib/epg.js';
import { categoriesOf, filterChannels } from '../../tdtlocal/static/js/lib/filters.js';
import { ago, dayLabel, hhmm } from '../../tdtlocal/static/js/lib/format.js';
import { fillAssignments, nextRotation } from '../../tdtlocal/static/js/lib/mosaic.js';
import { buildPlan, canPlayFormat, originNote, usesHls } from '../../tdtlocal/static/js/lib/plan.js';
import { initials, norm, slug } from '../../tdtlocal/static/js/lib/text.js';
import { newestFirst, parseU7d, programmeChannel, whenLabel } from '../../tdtlocal/static/js/lib/u7d.js';
import { ytEmbed, ytLive, ytVideoEmbed } from '../../tdtlocal/static/js/lib/youtube.js';

const ch = (id, name, extra = {}) => ({
  id, name, kind: 'tv', category: 'Nacionales', group: '', ok: null, sources: [{ u: `https://s/${id}.m3u8`, ok: null, f: '' }], ...extra,
});

test('text helpers', () => {
  assert.equal(norm('CANCIÓN Ñandú'), 'cancion nandu');
  assert.equal(slug('La 2 (HD)'), 'la 2 hd');
  assert.equal(initials('La Sexta'), 'LA');
  assert.equal(initials('¡Ñ!'), 'Ñ');
});

test('ytLive recognises channel lives and ytEmbed embeds videos', () => {
  const embed = (id) => 'https://www.youtube-nocookie.com/embed/' + id;
  assert.equal(ytLive('https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv/live'), true);
  assert.equal(ytLive('https://www.youtube.com/@SkyNews/live'), true);
  assert.equal(ytLive('https://www.youtube.com/@skynews/live?x=1'), true);
  assert.equal(ytLive('https://www.youtube.com/@SkyNews'), false);
  assert.equal(ytLive('https://www.youtube.com/watch?v=dQw4w9WgXcQ'), false);
  assert.equal(ytLive('https://www.youtube.com/live/dQw4w9WgXcQ'), true);
  assert.equal(ytLive('https://www.youtube.com/live/dQw4w9WgXcQ?x=1'), true);
  assert.equal(ytLive('https://example.com/@SkyNews/live'), false);
  assert.equal(ytEmbed('https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv/live'), null);
  assert.equal(ytEmbed('https://www.youtube.com/@SkyNews/live'), null);
  assert.equal(ytEmbed('https://www.youtube.com/@un-canal-que-no-existe/live'), null);
  assert.equal(ytVideoEmbed('dQw4w9WgXcQ'), embed('dQw4w9WgXcQ'));
  assert.equal(ytVideoEmbed('no'), null);
  assert.equal(ytEmbed('https://youtu.be/dQw4w9WgXcQ'), embed('dQw4w9WgXcQ'));
  assert.equal(ytEmbed('https://www.youtube.com/watch?v=dQw4w9WgXcQ'), embed('dQw4w9WgXcQ'));
  assert.equal(ytEmbed('https://www.youtube.com/watch?feature=x&v=dQw4w9WgXcQ&t=1'), embed('dQw4w9WgXcQ'));
  assert.equal(ytEmbed('https://www.youtube.com/live/dQw4w9WgXcQ'), null);
  assert.equal(ytEmbed('https://example.com/watch?v=dQw4w9WgXcQ'), null);
  assert.equal(ytEmbed('https://rtve.es/la1.m3u8'), null);
  assert.equal(ytEmbed(''), null);
});

test('epgOf finds what is on now and next', () => {
  const entries = [[100, 200, 'A'], [200, 300, 'B'], [300, 400, 'C']];
  assert.deepEqual(epgOf(entries, 150), { cur: entries[0], next: entries[1] });
  assert.deepEqual(epgOf(entries, 350), { cur: entries[2], next: null });
  assert.deepEqual(epgOf(entries, 50), { cur: null, next: entries[0] });
  assert.equal(epgOf(entries, 500), null);
  assert.equal(epgOf(undefined, 0), null);
});

test('epgLine, epgDetail and progress', () => {
  const entries = [[0, 100, 'Telediario'], [100, 200, 'El tiempo']];
  assert.equal(epgLine(entries, 50), 'Telediario');
  assert.equal(epgLine(entries, 1000), '');
  assert.match(epgLine([[100, 200, 'El tiempo']], 50), /· El tiempo$/);
  const lines = epgDetail(entries, 50);
  assert.match(lines[0], /^Ahora: Telediario \(/);
  assert.match(lines[1], /^Después \(.*\): El tiempo$/);
  assert.equal(progress(entries[0], 25), 25);
  assert.equal(progress(entries[0], -10), 0);
  assert.equal(progress(entries[0], 500), 100);
});

test('ago and dayLabel', () => {
  const now = 1_000_000;
  assert.equal(ago(0, now), 'nunca');
  assert.equal(ago(now - 30, now), 'ahora mismo');
  assert.match(ago(now - 600, now), /10 minutos/);
  assert.match(ago(now - 3 * 3600, now), /3 horas/);
  assert.match(ago(now - 5 * 86400, now), /5 días/);
  const today = new Date(2030, 5, 15, 12);
  const at = (d) => new Date(d).getTime() / 1000;
  assert.equal(dayLabel(at(new Date(2030, 5, 15, 23)), today), 'Hoy');
  assert.equal(dayLabel(at(new Date(2030, 5, 16, 1)), today), 'Mañana');
  assert.match(dayLabel(at(new Date(2030, 5, 20, 1)), today), /jueves/);
  assert.match(hhmm(at(new Date(2030, 5, 15, 9, 5))), /^09:05$/);
});

test('findChannel matches names with and without article, ignoring unplayable channels', () => {
  const channels = [
    ch('la-2', 'La 2'), ch('la-sexta', 'laSexta'), ch('trece', 'TRECE'), ch('trece-tv', 'TRECE TV Madrid'),
    ch('dead', 'Caído', { ok: false }), ch('nosrc', 'Solo web', { sources: [] }),
    ch('radio-la-2', 'La 2', { kind: 'radio' }),
  ];
  assert.equal(findChannel(channels, 'tv', 'la 2').id, 'la-2');
  assert.equal(findChannel(channels, 'radio', 'La 2').id, 'radio-la-2');
  assert.equal(findChannel(channels, 'tv', 'trece').id, 'trece');
  assert.equal(findChannel(channels, 'tv', 'el trece').id, 'trece');
  assert.equal(findChannel(channels, 'tv', 'trece tv').id, 'trece-tv');
  assert.equal(findChannel(channels, 'tv', 'Lasexta').id, 'la-sexta');
  assert.equal(findChannel(channels, 'tv', 'caido'), null);
  assert.equal(findChannel(channels, 'tv', 'solo web'), null);
  assert.equal(findChannel(channels, 'tv', 'tre'), null); // prefixes need 4+ letters
  assert.equal(findChannel(channels, 'tv', ''), null);
});

test('quickCommand', () => {
  const channels = [ch('la-1', 'La 1'), ch('la-2', 'La 2'), ch('trece', 'TRECE'), ch('toro', 'El Toro TV'), ch('r', 'Radio X', { kind: 'radio' })];
  const run = (t) => quickCommand(t, channels, 'tv');
  assert.deepEqual(run('radio'), [{ do: 'kind', kind: 'radio' }]);
  assert.deepEqual(run('la tele'), [{ do: 'kind', kind: 'tv' }]);
  assert.deepEqual(run('pon la 2'), [{ do: 'play', id: 'la-2' }]);
  assert.deepEqual(run('Ponme el canal trece'), [{ do: 'play', id: 'trece' }]);
  assert.deepEqual(run('pon La 2, TRECE y El Toro en multivisión rotando cada 30 s'),
    [{ do: 'multi', ids: ['la-2', 'trece', 'toro'], rotate: 30 }]);
  assert.deepEqual(run('ver la 1 en mosaico'), [{ do: 'multi', ids: ['la-1'], rotate: null }]);
  assert.deepEqual(run('pon la 1 + la 2 rota 15'), [{ do: 'multi', ids: ['la-1', 'la-2'], rotate: 15 }]);
  assert.equal(run('pon un canal que no existe'), null);
  assert.equal(run('pon la 2 y algo que no existe'), null);
  assert.equal(run('qué hay de fútbol'), null);
  assert.equal(run(''), null);
  const five = quickCommand('pon la 1, la 2, trece, el toro y la 1', channels, 'tv', 4);
  assert.equal(five[0].ids.length, 4);
});

test('nearestSecs snaps to the allowed intervals', () => {
  assert.equal(nearestSecs(1), 10);
  assert.equal(nearestSecs(22), 20);
  assert.equal(nearestSecs(28), 30);
  assert.equal(nearestSecs(500), 60);
});

test('filterChannels and categoriesOf', () => {
  const channels = [
    ch('a', 'La 1', { category: 'Nacionales' }),
    ch('b', 'Canal Sur', { category: 'Autonómicas', group: 'Andalucía' }),
    ch('c', 'Caído', { category: 'Nacionales', ok: false }),
    ch('d', 'Solo web', { category: 'Nacionales', sources: [] }),
    ch('e', 'Radio Uno', { kind: 'radio', category: 'Nacionales' }),
  ];
  const base = { kind: 'tv', onlyStream: true, hideDown: true, cat: '', grp: '', onlyFav: false, favs: new Set(), query: '' };
  const ids = (f) => filterChannels(channels, { ...base, ...f }).map((c) => c.id);
  assert.deepEqual(ids({}), ['a', 'b']);
  assert.deepEqual(ids({ hideDown: false }), ['a', 'b', 'c']);
  assert.deepEqual(ids({ onlyStream: false }), ['a', 'b', 'd']);
  assert.deepEqual(ids({ kind: 'radio' }), ['e']);
  assert.deepEqual(ids({ cat: 'Autonómicas' }), ['b']);
  assert.deepEqual(ids({ cat: 'Autonómicas', grp: 'Andalucía' }), ['b']);
  assert.deepEqual(ids({ onlyFav: true, favs: new Set(['a']) }), ['a']);
  assert.deepEqual(ids({ query: ' ANDALUCIA ' }), ['b']);
  assert.deepEqual(ids({ query: 'nacionales' }), ['a']);
  assert.deepEqual(categoriesOf(channels, 'tv', 'Autonómicas'), { cats: ['Nacionales', 'Autonómicas'], groups: ['Andalucía'] });
  assert.deepEqual(categoriesOf(channels, 'tv', '').groups, []);
});

test('fillAssignments prefers the saved tiles, then the following channels, healthy first', () => {
  const [a, b, c, d, e] = ['a', 'b', 'c', 'd', 'e'].map((id) => ch(id, id.toUpperCase()));
  const down = ch('down', 'Down', { ok: false });
  const view = [a, b, down, c, d, e];
  const out = fillAssignments([a, null, null, null], { savedIds: [null, 'e', null, null], view, channels: view, focusId: 'a' });
  assert.deepEqual(out.map((x) => x.id), ['a', 'e', 'b', 'c']); // saved e; then b, (down skipped), c
  const wrapped = fillAssignments([null, null], { savedIds: [], view, channels: view, focusId: 'd' });
  assert.deepEqual(wrapped.map((x) => x.id), ['e', 'a']);
  const sparse = fillAssignments([null, null], { savedIds: [], view: [a], channels: [a], focusId: null });
  assert.deepEqual(sparse, [a, null]);
  const noRepeat = fillAssignments([null, null], { savedIds: ['a', 'a'], view: [a, b], channels: [a, b], focusId: null });
  assert.deepEqual(noRepeat.map((x) => x.id), ['a', 'b']);
});

test('nextRotation skips tiles without signal', () => {
  const tile = (c, running = true, failed = false) => ({ c, running, failed });
  const tiles = [tile(1), tile(null), tile(2, true, true), tile(3)];
  assert.equal(nextRotation(tiles, 0), 3);
  assert.equal(nextRotation(tiles, 3), 0);
  assert.equal(nextRotation([tile(1), tile(null)], 0), 0); // only itself: wraps to the same tile
  assert.equal(nextRotation([tile(null), tile(null)], 0), -1);
  assert.equal(nextRotation([tile(1, false)], 0), -1);
});

test('buildPlan orders sources and honours format support', () => {
  const channel = {
    kind: 'tv',
    sources: [{ u: 'a', ok: false, f: '' }, { u: 'b', ok: null, f: '' }, { u: 'c', ok: true, f: '' }],
  };
  assert.deepEqual(buildPlan(channel).map((s) => s.u), ['c', 'b', 'a']);
  assert.deepEqual(buildPlan(channel, { pref: { u: 'a' } }).map((s) => s.u), ['a', 'c', 'b']);
  assert.deepEqual(buildPlan(channel, { onlyUrl: 'b' }).map((s) => s.u), ['b']);
  assert.deepEqual(buildPlan(channel)[0], { u: 'c', n: 3, f: '' });
  const radio = { kind: 'radio', sources: [{ u: 'x.aac', ok: true, f: 'aac' }, { u: 'x.mp3', ok: true, f: 'mp3' }] };
  assert.deepEqual(buildPlan(radio, { canPlayFmt: (f) => f === 'mp3' }).map((s) => s.u), ['x.mp3']);
  assert.equal(buildPlan(radio, { canPlayFmt: () => false }).length, 2); // nothing playable: try anyway
});

test('usesHls and canPlayFormat', () => {
  assert.equal(usesHls({ kind: 'tv' }, { u: 'a.mp3', f: '' }), true);
  assert.equal(usesHls({ kind: 'radio' }, { u: 'a.mp3', f: 'mp3' }), false);
  assert.equal(usesHls({ kind: 'radio' }, { u: 'a', f: 'm3u8' }), true);
  assert.equal(usesHls({ kind: 'radio' }, { u: 'https://x/a.m3u8?t=1', f: '' }), true);
  const audio = { canPlayType: (t) => (t === 'audio/mpeg' ? 'probably' : '') };
  assert.equal(canPlayFormat(audio, 'mp3'), true);
  assert.equal(canPlayFormat(audio, 'aac'), false);
  assert.equal(canPlayFormat(audio, 'stream'), true);
});

test('alert words', () => {
  assert.deepEqual(parseAlertWords('Terremoto, real madrid\nTerremoto, ab, ,  '), ['terremoto', 'real madrid']);
  assert.equal(parseAlertWords(Array.from({ length: 40 }, (_, i) => `palabra${i}`).join(',')).length, 20);
  assert.deepEqual(matchingWords('Ha habido un TERREMOTO en Lorca', ['terremoto', 'volcan']), ['terremoto']);
  assert.deepEqual(matchingWords('El Real Madrid gana', ['real madrid']), ['real madrid']);
  assert.deepEqual(matchingWords('nada', []), []);
});

test('originNote says what the origin answered, and what it usually means', () => {
  assert.equal(originNote(0), '');
  assert.match(originNote(492), /HTTP 492: el origen rechaza la petición/);
  assert.match(originNote(403), /HTTP 403: el origen rechaza/);
  assert.match(originNote(404), /ha caducado/);
  assert.match(originNote(503), /fallo del servidor/);
  assert.equal(originNote(302), ' El origen respondió HTTP 302.');
});

test('"solo con directo" hides list channels without a stream, but never the ones the user added', () => {
  const web = ch('web', 'Solo web', { sources: [] });
  const own = ch('own', 'Telecinco', { sources: [], custom: true });
  const f = { kind: 'tv', onlyStream: true, hideDown: false, cat: '', grp: '', onlyFav: false, favs: new Set(), query: '' };
  assert.deepEqual(filterChannels([web, own, ch('a', 'A')], f).map((c) => c.id), ['own', 'a']);
  assert.deepEqual(filterChannels([web, own], { ...f, onlyStream: false }).map((c) => c.id), ['web', 'own']);
});

test('buildPlan puts an embedded player after the streams, and never first by itself', () => {
  const player = 'https://www.atresplayer.com/embed/live/lasexta/';
  const stream = 'https://cdn.example/lasexta/master.m3u8';
  const c = ch('lasexta', 'La Sexta', { sources: [{ u: player, ok: null, f: '' }, { u: stream, ok: null, f: '' }] });
  assert.deepEqual(buildPlan(c).map((s) => s.u), [stream, player]); // the player was first in the list, and goes last
  assert.deepEqual(buildPlan(c, { pref: { u: player } }).map((s) => s.u), [player, stream]); // unless it is what the user chose
  assert.deepEqual(buildPlan(c, { onlyUrl: player }).map((s) => s.u), [player]);
  const down = ch('x', 'X', { sources: [{ u: player, ok: null, f: '' }, { u: stream, ok: false, f: '' }] });
  assert.deepEqual(buildPlan(down).map((s) => s.u), [stream, player]); // even a stream that failed its last check goes first
});

test('U7D sources round-trip and a programme becomes a playable channel', () => {
  assert.deepEqual(parseU7d('u7d:la-1:17281881'), { key: 'la-1', ref: '17281881' });
  assert.deepEqual(parseU7d('u7d:24h:5'), { key: '24h', ref: '5' });
  assert.equal(parseU7d('https://x/a.m3u8'), null);
  assert.equal(parseU7d('u7d:sinref'), null);
  const c = programmeChannel({ id: 'u7d-la-1', logo: 'L' }, { id: 'abc', title: 'Telediario', ts: 1 });
  assert.equal(c.id, 'u7d:la-1:abc');
  assert.equal(c.name, 'Telediario');
  assert.equal(c.kind, 'tv');
  assert.equal(c.vod, true);
  assert.equal(c.logo, 'L'); // no thumbnail: the logo of the channel
  assert.equal(c.thumb, false);
  const t = programmeChannel({ id: 'u7d-la-1', logo: 'L' }, { id: 'abc', title: 'X', ts: 1, image: 'https://i/x.jpg' });
  assert.equal(t.logo, 'https://i/x.jpg');
  assert.equal(t.thumb, true);
  assert.deepEqual(parseU7d(c.sources[0].u), { key: 'la-1', ref: 'abc' });
});

test('whenLabel says today, yesterday or the day', () => {
  const now = new Date(2026, 10, 1, 18, 0); // 1 Nov 2026, local time
  const at = (month, day, h, m) => new Date(2026, month, day, h, m).getTime() / 1000;
  assert.equal(whenLabel(at(10, 1, 17, 40), now), 'Hoy · 17:40');
  assert.equal(whenLabel(at(10, 1, 0, 5), now), 'Hoy · 00:05');
  assert.equal(whenLabel(at(9, 31, 21, 30), now), 'Ayer · 21:30');
  assert.match(whenLabel(at(9, 26, 21, 30), now), /26 oct.* · 21:30$/);
  assert.equal(whenLabel(0, now), '');
});

test('newestFirst sorts descending without touching the input', () => {
  const items = [{ ts: 1 }, { ts: 3 }, { ts: 2 }];
  assert.deepEqual(newestFirst(items).map((i) => i.ts), [3, 2, 1]);
  assert.deepEqual(items.map((i) => i.ts), [1, 3, 2]);
});

test('voice and typed commands never pick a U7D entry as a channel', () => {
  const live = { id: 'la-1', name: 'La 1', kind: 'tv', sources: [{ u: 'https://x/a.m3u8' }], ok: true };
  const recorded = { id: 'u7d-la-1', name: 'La 1', kind: 'u7d', sources: [{ u: 'u7d:la-1' }], ok: null };
  assert.equal(findChannel([recorded, live], 'tv', 'la 1').id, 'la-1');
  assert.equal(findChannel([recorded], 'tv', 'la 1'), null);
});
