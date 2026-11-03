import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

import { parseAddLink } from '../../tdtlocal/static/js/lib/addlink.js';
import { atresEmbed, atresWeb } from '../../tdtlocal/static/js/lib/embeds.js';

// tools/capturar-directo.js is a script to paste in a console: run it the way a test can, as a CommonJS module
const source = readFileSync(new URL('../../tools/capturar-directo.js', import.meta.url), 'utf8');
const sandbox = { module: { exports: {} }, console };
vm.runInNewContext(source, sandbox);
const { listas, principal, nombreDe, enlaceTdt } = sandbox.module.exports;

const entry = (name, startTime) => ({ name, startTime });

test('listas keeps the .m3u8 requests, once each, in order of first request, with the newest token', () => {
  const found = listas([
    entry('https://cdn.example/live/master.m3u8?token=a', 10),
    entry('https://cdn.example/live/seg1.ts', 11),
    entry('https://cdn.example/live/720p/chunklist.m3u8?token=a', 12),
    entry('https://cdn.example/live/720p/chunklist.m3u8?token=b', 20),
    entry('https://cdn.example/thumb.png', 21),
    entry('https://cdn.example/live/master.m3u8?token=c', 30),
  ]);
  assert.deepEqual(Array.from(found, (l) => l.url), [
    'https://cdn.example/live/master.m3u8?token=c',
    'https://cdn.example/live/720p/chunklist.m3u8?token=b',
  ]);
  assert.equal(listas([entry('https://x/a.mp4', 1), entry('https://x/notm3u8', 2)]).length, 0);
});

test('principal prefers the master playlist over one quality', () => {
  const l = (url, t) => ({ url, t });
  assert.equal(principal([l('https://c/720p/chunklist.m3u8?t=1', 1), l('https://c/live/index.m3u8?t=1', 2)]).url, 'https://c/live/index.m3u8?t=1');
  assert.equal(principal([l('https://c/live/master.m3u8', 1), l('https://c/live/chunklist_1.m3u8', 2)]).url, 'https://c/live/master.m3u8');
  assert.equal(principal([l('https://c/audio_1.m3u8', 1), l('https://c/video_1.m3u8', 2)]).url, 'https://c/audio_1.m3u8'); // nothing better: the first
});

test('nombreDe takes the channel out of a page title', () => {
  assert.equal(nombreDe('La Sexta en directo | atresplayer'), 'La Sexta');
  assert.equal(nombreDe('Antena 3 en directo - Atresplayer'), 'Antena 3');
  assert.equal(nombreDe(''), 'Canal propio');
  assert.equal(nombreDe('x'.repeat(200)).length, 80);
});

test('enlaceTdt builds the address with the fragment, and the Atresplayer headers only there', () => {
  const url = 'https://cdn.example/a/master.m3u8?hdnts=exp=1~hmac=ab&x=1';
  const atres = enlaceTdt(8788, url, 'La Sexta', 'www.atresplayer.com');
  assert.ok(atres.startsWith('http://127.0.0.1:8788/#add='));
  assert.ok(atres.endsWith('&hdr=atres'));
  assert.ok(!atres.includes('?hdnts')); // the link is encoded: it cannot be mistaken for the address's own query
  assert.ok(!enlaceTdt(8788, url, 'Otro', 'www.ejemplo.com').includes('hdr='));
  assert.ok(!enlaceTdt(8788, url, 'Otro', 'notatresplayer.com.evil.example').includes('hdr='));
});

test('what the snippet builds, the page understands', () => {
  const url = 'https://cdn.example/a/master.m3u8?hdnts=exp=1~hmac=ab&x=1';
  const link = enlaceTdt(8788, url, 'La Sexta', 'www.atresplayer.com');
  assert.deepEqual(parseAddLink(new URL(link).hash), { url, name: 'La Sexta', kind: 'tv', hdr: 'atres' });
});

test('parseAddLink only accepts a link to http(s), and cleans the rest', () => {
  assert.equal(parseAddLink(''), null);
  assert.equal(parseAddLink('#other=1'), null);
  assert.equal(parseAddLink('#add=javascript:alert(1)'), null);
  assert.equal(parseAddLink('#add=ftp://x/a.m3u8'), null);
  assert.equal(parseAddLink('#add=' + encodeURIComponent('https://x/a b.m3u8')), null);
  assert.equal(parseAddLink('#add=' + encodeURIComponent('https://x/' + 'a'.repeat(5000))), null);
  const ok = parseAddLink('#add=' + encodeURIComponent('https://x/a.m3u8') + '&name=' + 'N'.repeat(200) + '&kind=radio&hdr=raro');
  assert.equal(ok.url, 'https://x/a.m3u8');
  assert.equal(ok.name.length, 80);
  assert.equal(ok.kind, 'radio');
  assert.equal(ok.hdr, '');
  assert.equal(parseAddLink('#add=' + encodeURIComponent('https://x/a.m3u8') + '&kind=otro').kind, 'tv');
});

test('atresEmbed only recognises the official embeddable live player of Atresplayer', () => {
  assert.equal(atresEmbed('https://www.atresplayer.com/embed/live/lasexta/'), 'https://www.atresplayer.com/embed/live/lasexta/');
  assert.equal(atresEmbed('https://www.atresplayer.com/embed/live/LaSexta'), 'https://www.atresplayer.com/embed/live/lasexta/');
  assert.equal(atresEmbed('https://www.atresplayer.com/embed/live/antena3/?x=1'), 'https://www.atresplayer.com/embed/live/antena3/');
  assert.equal(atresEmbed('http://www.atresplayer.com/embed/live/lasexta/'), null);
  assert.equal(atresEmbed('https://www.atresplayer.com.evil.example/embed/live/lasexta/'), null);
  assert.equal(atresEmbed('https://evil.example/https://www.atresplayer.com/embed/live/lasexta/'), null);
  assert.equal(atresEmbed('https://www.atresplayer.com/directos/lasexta/'), null);
  assert.equal(atresEmbed('https://atres-live-ssai.atresmedia.com/x/master.m3u8'), null);
  assert.equal(atresEmbed(''), null);
  assert.equal(atresWeb('https://www.atresplayer.com/embed/live/lasexta/'), 'https://www.atresplayer.com/directos/lasexta/');
  assert.equal(atresWeb('https://example.org/a.m3u8'), '');
});
