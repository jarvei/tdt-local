// The plugin loader of the desktop app (desktop/lib/plugins.js). It does not need Electron.
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { discover, parsePattern, readEnabled, readPlugin, scriptsFor, writeEnabled } from '../../desktop/lib/plugins.js';

const REPO = new URL('../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');

function folder(root, name, manifest, files = { 'a.js': 'window.A = 1;' }) {
  const dir = join(root, name);
  mkdirSync(dir, { recursive: true });
  if (manifest !== null) writeFileSync(join(dir, 'manifest.json'), JSON.stringify(manifest));
  for (const [file, text] of Object.entries(files)) writeFileSync(join(dir, file), text);
  return dir;
}

const scripts = (extra = {}) => ({
  name: 'Prueba',
  version: '1.0',
  content_scripts: [{ matches: ['https://example.com/embed/*'], js: ['a.js'], all_frames: true, ...extra }],
});

function temp() {
  const root = mkdtempSync(join(tmpdir(), 'tdt-plugins-'));
  return { root, done: () => rmSync(root, { recursive: true, force: true }) };
}

test('match patterns follow the ones of Chrome extensions', () => {
  const p = parsePattern('https://*.example.com/embed/*');
  assert.ok(p.test('https://example.com/embed/x'));
  assert.ok(p.test('https://www.example.com/embed/x?y=1'));
  assert.ok(!p.test('https://evilexample.com/embed/x'));
  assert.ok(!p.test('http://example.com/embed/x'));
  assert.ok(!p.test('https://example.com/other'));
  assert.ok(parsePattern('*://example.com/*').test('http://example.com/a'));
  assert.ok(parsePattern('https://example.com:8443/*').test('https://example.com:8443/a'));
  assert.ok(!parsePattern('https://example.com/*').test('https://example.com:8443/a'));
  assert.ok(!parsePattern('https://a.com/*').test('not an address'));
});

test('a plugin cannot ask for every site, for this machine, or for odd addresses', () => {
  for (const bad of ['<all_urls>', '*://*/*', 'https://*/*', 'https://localhost/*', 'http://127.0.0.1:8788/*',
    'https://[::1]/*', 'file:///etc/*', 'https://exam*ple.com/*', 'https://example.com', 'ftp://example.com/*', '']) {
    assert.ok(parsePattern(bad).problem, bad);
  }
});

test('a good plugin is read and its scripts are found', () => {
  const { root, done } = temp();
  try {
    const plugin = readPlugin(folder(root, 'bueno', scripts()));
    assert.deepEqual(plugin.problems, []);
    assert.equal(plugin.name, 'Prueba');
    assert.equal(plugin.id, 'bueno');
  } finally { done(); }
});

test('what is wrong with a plugin is said, and it never runs', () => {
  const { root, done } = temp();
  try {
    const problems = (name, manifest, files) => readPlugin(folder(root, name, manifest, files)).problems.join(' | ');
    assert.match(problems('sin-manifest', null), /manifest\.json/);
    assert.match(problems('sin-scripts', { name: 'x' }), /content_scripts/);
    assert.match(problems('permisos', { ...scripts(), permissions: ['tabs'] }), /permissions/);
    assert.match(problems('hosts', { ...scripts(), host_permissions: ['<all_urls>'] }), /host_permissions/);
    assert.match(problems('fondo', { ...scripts(), background: { service_worker: 'a.js' } }), /background/);
    assert.match(problems('falta', scripts({ js: ['no-existe.js'] })), /no existe/);
    assert.match(problems('fuera', scripts({ js: ['../fuera.js'] }), {}), /fuera de la carpeta|no existe/);
    assert.match(problems('amplio', scripts({ matches: ['<all_urls>'] })), /patrón/);
    assert.equal(problems('vacios', { ...scripts(), permissions: [], host_permissions: [] }), '');
  } finally { done(); }
});

test('a script outside the folder is refused also through a link', (t) => {
  const { root, done } = temp();
  try {
    const outside = join(root, 'secreto.js');
    writeFileSync(outside, 'steal()');
    const dir = folder(root, 'enlace', scripts({ js: ['atajo.js'] }), {});
    try {
      symlinkSync(outside, join(dir, 'atajo.js'));
    } catch {
      t.skip('this system does not allow symbolic links');
      return;
    }
    assert.match(readPlugin(dir).problems.join(), /fuera de la carpeta/);
  } finally { done(); }
});

test('only enabled plugins without problems run, and only in the pages they declare', () => {
  const { root, done } = temp();
  try {
    folder(root, 'uno', scripts(), { 'a.js': 'ONE' });
    folder(root, 'roto', { ...scripts(), permissions: ['tabs'] }, { 'a.js': 'BROKEN' });
    const plugins = discover([root]);
    assert.deepEqual(plugins.map((p) => p.id), ['roto', 'uno']);
    const url = 'https://example.com/embed/live/x';
    assert.deepEqual(scriptsFor(plugins, new Set(), url, false), []); // nothing is on by default
    assert.deepEqual(scriptsFor(plugins, new Set(['roto']), url, false), []); // a broken one does not run even if on
    const [hit] = scriptsFor(plugins, new Set(['uno', 'roto']), url, false);
    assert.deepEqual(hit, { id: 'uno', scripts: [{ file: 'a.js', code: 'ONE' }] });
    assert.deepEqual(scriptsFor(plugins, new Set(['uno']), 'https://example.com/otra', false), []);
    assert.deepEqual(scriptsFor(plugins, new Set(['uno']), 'http://127.0.0.1:8788/', true), []);
  } finally { done(); }
});

test('a plugin without all_frames only acts on the top page', () => {
  const { root, done } = temp();
  try {
    folder(root, 'solo-arriba', scripts({ all_frames: false }));
    const plugins = discover([root]);
    const url = 'https://example.com/embed/x';
    assert.equal(scriptsFor(plugins, new Set(['solo-arriba']), url, false).length, 0);
    assert.equal(scriptsFor(plugins, new Set(['solo-arriba']), url, true).length, 1);
  } finally { done(); }
});

test('the first folder wins when two have a plugin with the same name, and folders without manifest are ignored', () => {
  const a = temp();
  const b = temp();
  try {
    folder(a.root, 'mismo', { ...scripts(), name: 'De A' });
    folder(b.root, 'mismo', { ...scripts(), name: 'De B' });
    folder(b.root, 'solo-b', scripts());
    mkdirSync(join(b.root, 'sin-manifest'));
    const found = discover([a.root, b.root, join(a.root, 'no-existe')]);
    assert.deepEqual(found.map((p) => [p.id, p.name]), [['mismo', 'De A'], ['solo-b', 'Prueba']]);
  } finally { a.done(); b.done(); }
});

test('the state of the plugins is saved and nothing is on if there is none', () => {
  const { root, done } = temp();
  try {
    const file = join(root, 'datos', 'plugins.json');
    assert.equal(readEnabled(file).size, 0);
    writeEnabled(file, new Set(['b', 'a']));
    assert.deepEqual([...readEnabled(file)], ['a', 'b']);
    writeFileSync(file, 'no es json');
    assert.equal(readEnabled(file).size, 0);
  } finally { done(); }
});


