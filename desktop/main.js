// TDT local as a desktop app: its own window (Chromium) around the Python server, a tray icon and plugins.
// Development: `npm install` and `npm start` inside desktop/ (needs Python and the repository). See docs/DESKTOP.md.
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { BrowserWindow, Menu, Tray, app, dialog, ipcMain, nativeImage, session, shell } from 'electron';
import { CACHE_MS, PLUGIN_ID, Reading, SLUGS, allowedAddress, listUrl, watchAddress } from './lib/atres.js';
import { Logbook, levelOf } from './lib/logbook.js';
import { discover, readEnabled, scriptsFor, writeEnabled } from './lib/plugins.js';
import { copyIfMissing, dataDirFor, desktopDirFor, readPort, writePort } from './lib/paths.js';
import { PREFERRED_PORTS, REPO_ROOT, serverCommandFor, startServer, stopServer } from './lib/server.js';

const here = fileURLToPath(new URL('.', import.meta.url));
const ICON = app.isPackaged ? join(process.resourcesPath, 'icon.png') : join(REPO_ROOT, 'packaging', 'icons', 'icon-256.png');

// What Chromium and the app keep lives inside the data folder of the server, and this must be set before anything reads
// it (the single-instance lock does). Before 1.3 it was %APPDATA%\tdt-local-desktop: the plugin choices come along.
const DATA_DIR = dataDirFor({ packaged: app.isPackaged, repoRoot: REPO_ROOT });
const previousUserData = app.getPath('userData');
app.setPath('userData', desktopDirFor(DATA_DIR));
copyIfMissing(join(previousUserData, 'plugins.json'), join(app.getPath('userData'), 'plugins.json'));

let server = null;
let logbook = new Logbook(); // in memory until the app is ready and knows where its folder is
const log = (source, level, text) => logbook.add(source, level, text);
const logsDir = () => join(app.getPath('userData'), 'logs');
let win = null;
let watching = null; // the window where a recording of Atresplayer is watched
let explorer = null; // the «Explorar Atresplayer» window and its reading: {win, reading}
const atresCache = new Map(); // channel -> {at, items}
let tray = null;
let quitting = false;
let plugins = [];
let enabled = new Set();

const stateFile = () => join(app.getPath('userData'), 'plugins.json');

/** Where plugins are looked for: the ones the user adds, then the ones that come with the app (in development, tools/). */
function pluginFolders() {
  const extra = process.env.TDT_PLUGINS_DIR ? process.env.TDT_PLUGINS_DIR.split(process.platform === 'win32' ? ';' : ':') : [];
  const bundled = app.isPackaged ? join(process.resourcesPath, 'plugins') : join(REPO_ROOT, 'tools');
  return [
    ...extra.map((dir) => ({ dir, label: 'TDT_PLUGINS_DIR' })),
    { dir: userPluginsDir(), label: 'tu carpeta de plugins' },
    { dir: bundled, label: 'incluido con la aplicación' },
  ];
}

const userPluginsDir = () => join(app.getPath('userData'), 'plugins');

/** What the settings page is told about the plugins (never the scripts, nor paths of the user's machine). */
function pluginList() {
  return {
    folder: userPluginsDir(),
    plugins: plugins.map((p) => ({
      id: p.id, name: p.name, version: p.version, description: p.description, source: p.source,
      sites: p.sites, problems: p.problems, enabled: enabled.has(p.id) && !p.problems.length,
    })),
  };
}

function loadPlugins() {
  plugins = discover(pluginFolders());
  enabled = readEnabled(stateFile());
  log('plugins', 'INFO', plugins.length + ' encontrados; activados: ' + ([...enabled].join(', ') || 'ninguno'));
  for (const p of plugins.filter((x) => x.problems.length)) log('plugins', 'WARN', p.id + ' no se puede usar: ' + p.problems.join('; '));
}

/** Sends what a window's page writes to its console to the log: everything from TDT local, and warnings and errors
 * from the players inside it (they are chatty). */
let reloads = 0;
let reloadTimer = null;
/** Our own server refused a connection: the page may have loaded without some of its scripts. Load it again, at most twice. */
function reloadOnce() {
  if (reloads >= 2 || reloadTimer || !win || win.isDestroyed()) return;
  reloadTimer = setTimeout(() => {
    reloadTimer = null;
    reloads += 1;
    log('app', 'WARN', 'el servidor rechazó una conexión al cargar la página: se recarga (' + reloads + ' de 2)');
    if (win && !win.isDestroyed()) win.webContents.reloadIgnoringCache();
  }, 1000);
}

let exploring = false; // the explorer's own session is already being watched
const seenRequests = new Set();
/** Requests that fail or answer with an error, once per address (the players inside the page do not report them in the
 * console, and they are often the reason a video does not start): at most `MAX_REQUEST_LINES` for the whole run. */
const MAX_REQUEST_LINES = 150;
function watchRequests(ses, label) {
  const note = (text, address) => {
    const key = text + ' ' + address.split('?')[0];
    if (seenRequests.has(key) || seenRequests.size >= MAX_REQUEST_LINES) return;
    seenRequests.add(key);
    log(label, 'WARN', text + ' ' + key.slice(text.length + 1, text.length + 201)); // the address without its parameters: they carry ids
  };
  ses.webRequest.onCompleted({ urls: ['<all_urls>'] }, (d) => {
    if (d.statusCode >= 400) note('HTTP ' + d.statusCode + ' ' + d.method, d.url);
  });
  ses.webRequest.onErrorOccurred({ urls: ['<all_urls>'] }, (d) => {
    if (d.error !== 'net::ERR_ABORTED') note(d.error + ' ' + d.method, d.url);
    if (d.error === 'net::ERR_CONNECTION_REFUSED' && server && d.url.startsWith(server.url)) reloadOnce();
  });
}

function watch(contents, label) {
  contents.on('console-message', (...args) => {
    const d = args.length === 1 ? args[0] : { level: args[1], message: args[2] }; // Electron changed this signature
    const name = typeof d.level === 'string' ? d.level : ['debug', 'info', 'warning', 'error'][d.level] || 'info';
    const message = String(d.message || '');
    const own = label === 'pagina' && d.frame && d.frame.parent === null;
    if (!own && name !== 'warning' && name !== 'error' && !message.startsWith('[TDT')) return;
    log(label, name === 'error' ? 'ERROR' : name === 'warning' ? 'WARN' : 'INFO', message);
  });
  contents.on('did-fail-load', (_e, code, text, url, isMainFrame) => {
    if (code !== -3) log(label, 'ERROR', (isMainFrame ? 'no se pudo cargar ' : 'no se pudo cargar un marco: ') + url + ' (' + text + ')');
  });
  contents.on('render-process-gone', (_e, details) => log(label, 'ERROR', 'el proceso de la página se cerró: ' + details.reason));
  contents.on('unresponsive', () => log(label, 'WARN', 'la página no responde'));
}

function serverCommand() {
  return serverCommandFor({ packaged: app.isPackaged, resourcesPath: process.resourcesPath, repoRoot: REPO_ROOT, dataDir: DATA_DIR });
}

function setPlugin(id, on) {
  const plugin = plugins.find((p) => p.id === id);
  if (!plugin || (on && plugin.problems.length)) return false;
  if (on) enabled.add(id);
  else enabled.delete(id);
  writeEnabled(stateFile(), enabled);
  log('plugins', 'INFO', id + (on ? ' activado' : ' desactivado'));
  buildTray();
  return true;
}

function togglePlugin(id, on) {
  setPlugin(id, on);
  if (win) win.webContents.reloadIgnoringCache(); // the plugin is injected when the page loads
}

function buildTray() {
  if (!tray) return;
  const items = plugins.length
    ? plugins.map((p) => ({
        label: p.problems.length ? `${p.name} (no se puede usar)` : p.name,
        type: 'checkbox',
        checked: enabled.has(p.id) && !p.problems.length,
        enabled: !p.problems.length,
        toolTip: p.problems.length ? p.problems.join('\n') : p.description,
        click: (item) => togglePlugin(p.id, item.checked),
      }))
    : [{ label: 'No hay plugins', enabled: false }];
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Mostrar TDT local', click: showWindow },
    { label: 'Plugins', submenu: items },
    { type: 'separator' },
    { label: 'Salir', click: () => app.quit() },
  ]));
}

function showWindow() {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

function createWindow(url) {
  win = new BrowserWindow({
    width: 1280,
    height: 800,
    backgroundColor: '#0b0d10',
    icon: ICON,
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(here, 'preload.cjs'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      nodeIntegrationInSubFrames: true, // so the preload also runs in the players inside the page
      autoplayPolicy: 'no-user-gesture-required',
    },
  });
  win.setMenu(null);
  watch(win.webContents, 'pagina');
  const origin = new URL(url).origin;
  // the window shows TDT local; everything else opens in the system browser
  win.webContents.setWindowOpenHandler(({ url: target }) => {
    if (/^https?:/.test(target)) shell.openExternal(target);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (event, target) => {
    if (new URL(target).origin !== origin) {
      event.preventDefault();
      if (/^https?:/.test(target)) shell.openExternal(target);
    }
  });
  win.on('closed', () => { win = null; });
  win.loadURL(url);
}

// «Explorar Atresplayer»: a window of the app on the public page «Emitido en TV (U7D)» of a channel, so that the plugin
// «atresplayer-u7d» reads the recordings from it. It is shown while it works (the cookie notice and anything else on the
// page is the user's to answer), has its own browsing data, can only go to atresplayer.com, and closes when it has read.
function exploreAtres(slug) {
  const cached = atresCache.get(slug);
  if (cached && Date.now() - cached.at < CACHE_MS) return Promise.resolve({ items: cached.items });
  if (!plugins.some((p) => p.id === PLUGIN_ID && !p.problems.length) || !enabled.has(PLUGIN_ID)) {
    return Promise.resolve({ error: 'plugin-off', plugin: PLUGIN_ID });
  }
  if (explorer) return Promise.resolve({ error: 'busy' });
  return new Promise((resolve) => {
    const window = new BrowserWindow({
      width: 1100, height: 800, parent: win || undefined, title: 'Explorar Atresplayer · ' + slug, icon: ICON, autoHideMenuBar: true,
      webPreferences: { partition: 'persist:atresplayer', preload: join(here, 'preload.cjs'), contextIsolation: true, sandbox: true, nodeIntegration: false },
    });
    window.setMenu(null);
    watch(window.webContents, 'explorar');
    log('explorar', 'INFO', 'se abre ' + listUrl(slug));
    const timer = setTimeout(() => reading.end('tiempo agotado'), 120000);
    const reading = new Reading(slug, (result) => {
      clearTimeout(timer);
      log('explorar', result.items ? 'INFO' : 'WARN', result.items ? 'leídos ' + result.items.length + ' programas de ' + slug : 'sin datos de ' + slug + ': ' + result.error);
      if (result.items) atresCache.set(slug, { at: Date.now(), items: result.items });
      explorer = null;
      if (!window.isDestroyed()) window.destroy();
      showWindow(); // the page was behind the explorer: until it is shown again the browser does not load its pictures
      resolve(result);
    });
    explorer = { win: window, reading };
    window.webContents.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
    if (!exploring) { exploring = true; watchRequests(window.webContents.session, 'red-explorar'); } // the session outlives the window
    window.webContents.setWindowOpenHandler(({ url }) => {
      if (/^https?:/.test(url)) shell.openExternal(url);
      return { action: 'deny' };
    });
    window.webContents.on('will-navigate', (event, target) => {
      if (!allowedAddress(target)) {
        event.preventDefault();
        if (/^https?:/.test(target)) shell.openExternal(target);
      }
    });
    window.on('closed', () => reading.end('ventana cerrada'));
    window.loadURL(listUrl(slug)).catch(() => reading.end('no se pudo abrir la página'));
  });
}

// Watching a recording: Atresplayer's own page of it, in a window of the app. Its embedded player does not play recordings
// (it answers 404), but the page does. The window shares its data with the explorer, so the answer to the cookie notice
// (which is the user's) is remembered; it can only go to atresplayer.com; a new recording replaces the one being watched.
function watchAtres(address, title) {
  if (watching && !watching.isDestroyed()) {
    watching.setTitle(title);
    watching.loadURL(address).catch(() => {});
    watching.show();
    watching.focus();
    log('ver', 'INFO', 'se abre ' + address);
    return;
  }
  const window = new BrowserWindow({
    width: 1100, height: 720, parent: win || undefined, title, icon: ICON, autoHideMenuBar: true,
    webPreferences: {
      partition: 'persist:atresplayer', preload: join(here, 'preload.cjs'), contextIsolation: true, sandbox: true,
      nodeIntegration: false, autoplayPolicy: 'no-user-gesture-required',
    },
  });
  watching = window;
  window.setMenu(null);
  watch(window.webContents, 'ver');
  window.webContents.session.setPermissionRequestHandler((_wc, permission, callback) => callback(permission === 'fullscreen'));
  if (!exploring) { exploring = true; watchRequests(window.webContents.session, 'red-explorar'); }
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, target) => {
    if (!allowedAddress(target)) {
      event.preventDefault();
      if (/^https?:/.test(target)) shell.openExternal(target);
    }
  });
  window.on('closed', () => { if (watching === window) watching = null; });
  log('ver', 'INFO', 'se abre ' + address);
  window.loadURL(address).catch((error) => log('ver', 'ERROR', 'no se pudo abrir la página: ' + (error.message || error)));
}

// The preload asks which scripts belong to the frame it runs in. The address comes from Electron (the frame that
// asks), never from what the page says, and only frames of our window are answered.
ipcMain.on('tdt:plugin-scripts', (event) => {
  const frame = event.senderFrame;
  const ours = (win && !win.isDestroyed() && event.sender === win.webContents)
    || (explorer && !explorer.win.isDestroyed() && event.sender === explorer.win.webContents);
  event.returnValue = ours && frame ? scriptsFor(plugins, enabled, frame.url, frame.parent === null) : [];
});

// For development only: lets a test script drive the window (Chrome DevTools protocol, this machine only).
if (!app.isPackaged && process.env.TDT_DEBUG_PORT) {
  app.commandLine.appendSwitch('remote-debugging-port', process.env.TDT_DEBUG_PORT);
  app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1');
}

// What the settings page may ask of the app. Only the top page of our window is answered: not a frame inside it, and not
// any other address.
const fromOurPage = (event) => {
  const frame = event.senderFrame;
  return !!(win && !win.isDestroyed() && server && frame && event.sender === win.webContents && frame.parent === null
    && new URL(frame.url).origin === new URL(server.url).origin);
};
ipcMain.handle('tdt:plugins-list', (event) => (fromOurPage(event) ? pluginList() : null));
ipcMain.handle('tdt:plugins-set', (event, id, on) => {
  if (!fromOurPage(event) || typeof id !== 'string' || typeof on !== 'boolean') return null;
  setPlugin(id, on);
  return pluginList();
});
ipcMain.handle('tdt:plugins-folder', async (event) => {
  if (!fromOurPage(event)) return false;
  mkdirSync(userPluginsDir(), { recursive: true });
  return (await shell.openPath(userPluginsDir())) === '';
});
ipcMain.handle('tdt:plugins-rescan', (event) => {
  if (!fromOurPage(event)) return null;
  loadPlugins();
  buildTray();
  return pluginList();
});
ipcMain.handle('tdt:atres-list', (event, slug) => {
  if (!fromOurPage(event) || !SLUGS.includes(slug)) return { error: 'no permitido' };
  return exploreAtres(slug);
});
ipcMain.handle('tdt:atres-watch', (event, address, title) => {
  const page = watchAddress(address);
  if (!fromOurPage(event) || !page) return false;
  watchAtres(page, String(title || 'Atresplayer').slice(0, 120));
  return true;
});
// what the plugin read: only from the explorer window, its top page, atresplayer.com, and with the plugin on
ipcMain.on('tdt:u7d-read', (event, msg) => {
  const frame = event.senderFrame;
  if (!explorer || event.sender !== explorer.win.webContents || !frame || frame.parent !== null
    || !allowedAddress(frame.url) || !enabled.has(PLUGIN_ID)) return;
  explorer.reading.message(msg);
});
// the log, for the settings page: read it, save it to a file the user chooses, open its folder
ipcMain.handle('tdt:logs-get', (event) => (fromOurPage(event) ? { lines: logbook.lines(1500), folder: logsDir() } : null));
ipcMain.handle('tdt:logs-save', async (event) => {
  if (!fromOurPage(event)) return { saved: '' };
  const pick = await dialog.showSaveDialog(win, { defaultPath: 'tdt-local-registro.txt', filters: [{ name: 'Texto', extensions: ['txt', 'log'] }] });
  if (pick.canceled || !pick.filePath) return { saved: '' };
  const head = ['TDT local ' + app.getVersion() + ' · Electron ' + process.versions.electron + ' · Chromium ' + process.versions.chrome + ' · ' + process.platform + ' ' + process.arch, ''];
  writeFileSync(pick.filePath, head.concat(logbook.lines()).join('\n') + '\n', 'utf8');
  return { saved: pick.filePath };
});
ipcMain.handle('tdt:logs-folder', async (event) => {
  if (!fromOurPage(event)) return false;
  mkdirSync(logsDir(), { recursive: true });
  return (await shell.openPath(logsDir())) === '';
});
ipcMain.handle('tdt:reload', (event) => {
  if (!fromOurPage(event)) return false;
  win.webContents.reloadIgnoringCache();
  return true;
});

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) app.quit();
else {
  app.on('second-instance', showWindow);

  app.whenReady().then(async () => {
    // Permissions a site could ask for (camera, location, notifications...) are all refused; the page needs none.
    session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => callback(permission === 'fullscreen'));
    watchRequests(session.defaultSession, 'red');
    logbook = new Logbook({ file: join(logsDir(), 'tdt-local.log') });
    log('app', 'INFO', 'TDT local ' + app.getVersion() + ' · Electron ' + process.versions.electron + ' · Chromium ' + process.versions.chrome
      + ' · ' + process.platform + ' ' + process.arch + (app.isPackaged ? '' : ' · desarrollo'));
    loadPlugins();
    try {
      const portFile = join(app.getPath('userData'), 'port.json');
      const remembered = readPort(portFile);
      server = await startServer(serverCommand(), {
        onLine: (line) => log('servidor', levelOf(line), line),
        ports: [remembered, ...PREFERRED_PORTS],
      });
      if (remembered && server.port !== remembered) {
        log('app', 'WARN', 'el puerto ' + remembered + ' estaba ocupado: se usa el ' + server.port + ' y la página empieza sin lo que guardó');
      }
      writePort(portFile, server.port);
      log('app', 'INFO', 'servidor listo en ' + server.url);
    } catch (error) {
      log('app', 'ERROR', 'el servidor no arrancó: ' + (error.message || error));
      console.error(error);
      const { dialog } = await import('electron');
      dialog.showErrorBox('TDT local no pudo arrancar', String(error.message || error));
      app.exit(1);
      return;
    }
    server.child.once('exit', (code) => {
      log('app', quitting ? 'INFO' : 'WARN', 'el servidor se cerró (código ' + code + ')');
      if (!quitting) app.quit();
    }); // the «Salir» button of the page stops the server
    createWindow(server.url);
    tray = new Tray(nativeImage.createFromPath(ICON).resize({ width: 24, height: 24 }));
    tray.setToolTip('TDT local');
    tray.on('click', showWindow);
    buildTray();
  });

  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', (event) => {
    if (quitting || !server) return;
    quitting = true;
    event.preventDefault();
    stopServer(server).finally(() => app.exit(0));
  });
}
