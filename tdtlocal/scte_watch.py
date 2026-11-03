"""Ad-break watcher: follows the channel being watched and publishes when a break starts and ends.

Two things feed the log:

* **the live watcher** (``ads.engine``, the same detection the operator's ``tdt-datos`` project uses):
  while a channel is tuned, its media playlist is polled, its markers are decoded and the state is
  kept. The change to *ad break* and back to *programme* is written to the log already timed for the
  picture — the playlist runs ahead of the player, so it is evaluated as of ``now - lag``, the way
  the TDT Spain app's ``AdMute`` does it.
* **what the proxy serves**, for channels nobody is watching: markers are read on their way through.

The log lives in ``scte.jsonl`` (one JSON object per line) under the data folder.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import tdtspain
from .adwatch import MEDIASET_LAG_S, Vigilance
from .adwatch.engine import INTERVAL_S, LAG_S, MARGIN_S, MEDIASET_INTERVAL_S, MEDIASET_MARGIN_S
from .config import Config
from .custom import is_youtube
from .health import HLS_URL
from .net import Net
from .scte import Marker, scan_playlist, segment_markers, strip_query
from .store import Store, source_ok
from .util import normalize

log = logging.getLogger(__name__)

TICK_SECONDS = 2  # how often the background loop looks at the settings
PROBE_TIMEOUT = 8
SCAN_WORKERS = 8
SAMPLE_BYTES = 262144  # enough for the PAT and PMT, which repeat every ~100 ms
MAX_PLAYLIST = 4_000_000
MAX_TARGETS = 800
MAX_REMEMBERED = 5000
DEFAULT_TTL = 21600
TTL = {"cue-out-cont": 600}
BREAK_MARGIN = 15
UNKNOWN_BREAK = 30
PLACEHOLDER = ("[", "{")  # list entries with template variables ([DEVICE_ID]...) are not real URLs


class ScteLog:
    """The events found so far: in memory, and appended to a file so they survive a restart."""

    def __init__(self, path: Path, keep: int) -> None:
        self.path = path
        self.keep = keep
        self._events: list[dict[str, Any]] = []
        self._next_id = 1
        self._lines = 0  # lines in the file, to know when to compact it
        self._warned = False
        self._lock = threading.Lock()

    def load(self) -> None:
        events: list[dict[str, Any]] = []
        try:
            with self.path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and isinstance(event.get("id"), int):
                        events.append(event)
        except OSError:
            return
        with self._lock:
            self._events = events[-self.keep :]
            self._next_id = max((e["id"] for e in events), default=0) + 1
            self._lines = len(events)
            if len(events) > len(self._events):
                self._rewrite()

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    @property
    def last_id(self) -> int:
        with self._lock:
            return self._next_id - 1

    def add(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            event = {**event, "id": self._next_id}
            self._next_id += 1
            self._events.append(event)
            self._append(event)
            if len(self._events) > self.keep:
                del self._events[: len(self._events) - self.keep]
            if self._lines > self.keep * 1.25 + 50:
                self._rewrite()
            return event

    def since(self, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        """The newest ``limit`` events with an id above ``after``, oldest first."""
        with self._lock:
            found = [e for e in self._events if e["id"] > after]
        return found[-limit:] if limit > 0 else []

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def summary(self) -> list[dict[str, Any]]:
        """Per channel: how many markers, of which kinds, and when the last one was seen."""
        per: dict[str, dict[str, Any]] = {}
        for event in self.all():
            row = per.setdefault(
                event["channel"], {"channel": event["channel"], "name": event.get("name", ""), "count": 0, "kinds": {}}
            )
            row["count"] += 1
            row["last_ts"] = event["ts"]
            row["kinds"][event["kind"]] = row["kinds"].get(event["kind"], 0) + 1
        return sorted(per.values(), key=lambda r: (-r["count"], r["name"]))

    def set_keep(self, keep: int) -> None:
        with self._lock:
            self.keep = keep
            if len(self._events) > keep:
                del self._events[: len(self._events) - keep]
                self._rewrite()

    def clear(self) -> int:
        with self._lock:
            count = len(self._events)
            self._events = []
            self._lines = 0
            try:
                self.path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("no se pudo borrar el registro de marcas: %s", exc)
            return count

    def _append(self, event: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._lines += 1
        except OSError as exc:
            if not self._warned:  # once: the log still works in memory
                self._warned = True
                log.warning("no se puede guardar el registro de marcas (%s): solo queda en memoria", exc)

    def _rewrite(self) -> None:
        """Atomic: keep only the events in memory."""
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self._events), encoding="utf-8")
            tmp.replace(self.path)
            self._lines = len(self._events)
        except OSError as exc:
            log.warning("no se pudo compactar el registro de marcas: %s", exc)


class ScteWatcher:
    """Follows the tuned channel with the ``tdt-datos`` engine and writes the changes to the log."""

    def __init__(self, config: Config, store: Store, net: Net, path: Path | None = None) -> None:
        self.config = config
        self.store = store
        self.net = net
        self.log = ScteLog(path or config.data_dir / "scte.jsonl", config.scte_keep)
        self._seen: dict[str, float] = {}  # dedupe key -> when it was last reported
        self._breaks: dict[str, float] = {}  # channel id -> until when an ad break announced for it is under way
        self._owner: dict[str, tuple[str, str]] = {}  # playlist/segment URL -> (channel id, name)
        self._sources_signature: tuple[int, ...] = ()
        self._sources: dict[str, tuple[str, str]] = {}
        self._vigilances: dict[str, Vigilance] = {}
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()  # the app's: set when it closes, so a scan under way skips what is left
        self._scan_requested = False
        self._state: dict[str, Any] = {"running": False, "last": None, "done": 0, "total": 0, "errors": 0, "error": ""}

    # ---- life cycle
    def load(self) -> None:
        """Read the log of previous runs, so that a restart does not report the same markers again."""
        self.log.load()
        for event in self.log.all():
            if event.get("key"):
                self._seen[event["key"]] = float(event.get("ts", 0))

    def reconfigure(self) -> None:
        """The settings changed: apply the log size and, if scanning, start a scan now."""
        self.log.set_keep(self.config.scte_keep)
        if not self.config.scte_enabled:
            self.unwatch()
        if self._scans():
            self._scan_requested = True
        self._wake.set()

    def _scans(self) -> bool:
        return self.config.scte_enabled and self.config.scte_scope != "viewing"

    def run(self, stop: threading.Event) -> None:
        """Background loop: an active scan every ``scte_interval`` seconds, when the settings ask for one."""
        self._stop = stop
        next_scan = 0.0
        while not stop.is_set():
            requested, self._scan_requested = self._scan_requested, False
            if self._scans() and (requested or time.time() >= next_scan):
                try:
                    self.scan()
                except Exception:
                    log.exception("fallo inesperado al buscar marcas de anuncios")
                next_scan = time.time() + self.config.scte_interval
            if self._wake.wait(TICK_SECONDS):
                self._wake.clear()

    def request_scan(self) -> tuple[bool, str]:
        """Ask for a scan at the next tick. ``(accepted, reason it was not)``."""
        if not self.config.scte_enabled:
            return False, "La vigilancia de marcas está desactivada."
        if self.config.scte_scope == "viewing":
            return False, "Con «solo lo que veo» no hay exploración: elige una lista de canales o todos."
        if self._scan_lock.locked():
            return False, "Ya hay una exploración en curso."
        self._scan_requested = True
        self._wake.set()
        return True, ""

    def clear(self) -> int:
        """Empty the log. What was seen is forgotten too, so the same markers are reported again if they return."""
        with self._lock:
            self._seen.clear()
            self._breaks.clear()
        return self.log.clear()

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
            watched = [v.snapshot() for v in self._vigilances.values()]
        return {
            "enabled": self.config.scte_enabled,
            "scope": self.config.scte_scope,
            "interval": self.config.scte_interval,
            "inspect": self.config.scte_inspect_segments,
            "events": len(self.log),
            "scan": state,
            "states": watched,
        }

    # ---- the live watcher
    def watch(self, channel: dict[str, Any], resolve: Any = None) -> dict[str, Any]:
        """Start following ``channel``. ``resolve`` is a callable returning ``{url, headers}`` on demand."""
        cid = channel["id"]
        source = next((u for u in channel.get("sources") or [] if u), "")
        headers: dict[str, str] = {}
        resolver = None
        mediaset = False
        url = ""
        if tdtspain.is_tdtspain(source):
            recipe = channel.get("tdtspain") or {}
            mediaset = str(recipe.get("streamtype") or "") == "stream10"
            if resolve is None:
                return {"ok": False, "error": "canal sin resolvedor"}
            out = resolve()
            url = str(out.get("url") or "")
            headers = dict(out.get("headers") or {})
            resolver = resolve
        elif source and not is_youtube(source) and HLS_URL.search(source):
            url = source
            headers = self.store.headers_for(cid)
        else:
            return {"ok": False, "error": "este canal no tiene un directo abierto que vigilar"}
        if not url:
            return {"ok": False, "error": "sin URL"}
        with self._lock:
            old = self._vigilances.pop(cid, None)
        if old is not None:
            old.close()
        watcher = Vigilance(
            cid,
            channel["name"],
            url,
            headers,
            self.net,
            resolver=resolver,
            lag=MEDIASET_LAG_S if mediaset else LAG_S,
            margin=MEDIASET_MARGIN_S if mediaset else MARGIN_S,
            interval=MEDIASET_INTERVAL_S if mediaset else INTERVAL_S,
            on_change=lambda state, since, left, event, previous: self._on_change(
                channel, state, since, left, event, previous
            ),
            stop=self._stop,
            mediaset=mediaset,
        )
        with self._lock:
            self._vigilances[cid] = watcher
        watcher.start()
        log.info("vigilando %s (%s)", channel["name"], "Mediaset" if mediaset else "HLS")
        return {"ok": True, "state": watcher.state}

    def unwatch(self, cid: str = "") -> bool:
        """Stop following one channel, or every channel when ``cid`` is empty."""
        with self._lock:
            if cid:
                watcher = self._vigilances.pop(cid, None)
                watchers = [watcher] if watcher else []
            else:
                watchers = list(self._vigilances.values())
                self._vigilances.clear()
        for watcher in watchers:
            watcher.close()
        return bool(watchers)

    def states(self) -> list[dict[str, Any]]:
        with self._lock:
            return [v.snapshot() for v in self._vigilances.values()]

    def _on_change(
        self,
        channel: dict[str, Any],
        state: str,
        since: str | None,
        left: int | None,
        event: Any,
        previous: str = "",
    ) -> None:
        """A break starts or ends *for the picture*: write it to the log so the page reacts now."""
        if not self.config.scte_enabled:
            return
        if state == "publicidad":
            kind = "cue-out"
        elif state == "programa" and previous == "publicidad":
            kind = "cue-in"
        else:
            return  # the first reading only tells us the channel is on programme: there is nothing to close
        detail = "corte de publicidad" if kind == "cue-out" else "fin de la publicidad"
        if event and event.get("detail"):
            detail = str(event["detail"])
        now = int(time.time())
        entry: dict[str, Any] = {
            "ts": now,
            "channel": channel["id"],
            "name": channel["name"],
            "url": "",
            "kind": kind,
            "detail": detail,
            "raw": "",
            "source": "vigilancia",
            "key": f"{channel['id']}|{kind}|{since or ''}",
            "duration": left or 0,
            "apply_now": True,
            "state": state,
            "since": since,
        }
        with self._lock:
            duplicate = self._seen.get(entry["key"], 0.0) > now - 5
            self._seen[entry["key"]] = now
        if not duplicate:
            self.log.add(entry)
            log.info("marca de anuncios en %s: %s %s", channel["name"], kind, detail)

    # ---- what the proxy serves
    def observe(self, url: str, text: str, tag: str = "") -> None:
        """Look at a playlist the proxy is serving. Never raises: it must not get in the way of playback."""
        if not self.config.scte_enabled:
            return
        try:
            channel = self._channel_of(url, tag)
            with self._lock:
                if channel[0] in self._vigilances:
                    return  # the live watcher owns this channel: it publishes the state already
            self._remember_children(url, text, channel)
            self._record(channel, scan_playlist(text), "reproducción", url)
        except Exception:
            log.debug("no se pudo analizar una lista en busca de marcas", exc_info=True)

    def _channel_of(self, url: str, tag: str) -> tuple[str, str]:
        key = strip_query(url)
        with self._lock:
            known = self._owner.get(key)
        if known:
            return known
        known = self._source_index().get(key)
        if known:
            return known
        if tag:
            with self.store.lock:
                for channel in self.store.all_channels():
                    if channel["id"] == tag:
                        return channel["id"], channel["name"]
        host = urllib.parse.urlsplit(url).hostname or "desconocido"
        return host, host

    def _source_index(self) -> dict[str, tuple[str, str]]:
        """``stream URL (no query) -> (channel id, name)``, rebuilt when the lists change."""
        store = self.store
        with store.lock:
            signature = (len(store.channels), len(store.radios), len(store.custom))
            if signature != self._sources_signature:
                self._sources = {
                    strip_query(url): (ch["id"], ch["name"])
                    for ch in store.all_channels()
                    for url in ch["sources"]
                    if not tdtspain.is_tdtspain(url)
                }
                self._sources_signature = signature
            return self._sources

    def _remember_children(self, url: str, text: str, channel: tuple[str, str]) -> None:
        """The playlists and segments a playlist points to belong to the same channel."""
        children = set()
        for raw in text.splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                children.add(line)
            elif 'URI="' in line:
                children.add(line.split('URI="', 1)[1].split('"', 1)[0])
        with self._lock:
            if len(self._owner) > MAX_REMEMBERED:
                self._owner.clear()
            for child in children:
                self._owner[strip_query(urllib.parse.urljoin(url, child))] = channel

    # ---- the log
    def _record(self, channel: tuple[str, str], markers: list[Marker], source: str, url: str) -> int:
        """Report the markers that are new. Returns how many."""
        channel_id, name = channel
        now = time.time()
        fresh: list[tuple[Marker, str]] = []
        with self._lock:
            for marker in markers:
                kind = marker["kind"]
                key = f"{channel_id}|{kind}|{marker['ident']}"
                if now - self._seen.get(key, 0.0) < TTL.get(kind, DEFAULT_TTL):
                    continue
                self._seen[key] = now
                if kind == "cue-out":
                    if now < self._breaks.get(channel_id, 0.0):
                        continue  # the same break announced again (another tag, or every segment repeats it)
                    length = marker.get("duration") or UNKNOWN_BREAK
                    self._breaks[channel_id] = now + length + BREAK_MARGIN
                elif kind == "cue-in":
                    self._breaks.pop(channel_id, None)
                fresh.append((marker, key))
            if len(self._seen) > MAX_REMEMBERED:
                horizon = now - DEFAULT_TTL
                self._seen = {k: t for k, t in self._seen.items() if t > horizon}
        for marker, key in fresh:
            event: dict[str, Any] = {
                "ts": int(now),
                "channel": channel_id,
                "name": name,
                "url": strip_query(url),
                "kind": marker["kind"],
                "detail": marker["detail"],
                "raw": marker["raw"],
                "source": source,
                "key": key,
            }
            if "scte" in marker:
                event["scte"] = marker["scte"]
            for extra in ("seg", "lead", "duration", "elapsed"):  # where in the stream and how long: for reactions
                if extra in marker:
                    event[extra] = marker[extra]
            self.log.add(event)
            log.info("marca de anuncios en %s: %s %s", name, marker["kind"], marker["detail"])
        return len(fresh)

    # ---- active scan
    def _targets(self) -> list[dict[str, Any]]:
        """One HLS stream per channel to look at, according to the scope."""
        cfg = self.config
        wanted = {normalize(t.strip()) for t in cfg.scte_channels.split(",") if t.strip()}
        if cfg.scte_scope == "list" and not wanted:
            return []
        targets: list[dict[str, Any]] = []
        with self.store.lock:
            status = dict(self.store.status)
            headers = dict(self.store.custom_headers)
            for ch in self.store.all_channels():
                if cfg.scte_scope == "list" and not {normalize(ch["name"]), normalize(ch["id"])} & wanted:
                    continue
                url = next(
                    (
                        u
                        for u in ch["sources"]
                        if HLS_URL.search(u)
                        and not tdtspain.is_tdtspain(u)
                        and not is_youtube(u)
                        and not any(p in u for p in PLACEHOLDER)
                        and source_ok(ch, u, status) is not False
                    ),
                    None,
                )
                if url:
                    targets.append(
                        {"id": ch["id"], "name": ch["name"], "url": url, "headers": headers.get(ch["id"], {})}
                    )
        return targets[:MAX_TARGETS]

    def _read_playlist(self, url: str, headers: dict[str, str]) -> tuple[str, str]:
        with self.net.open(url, headers, False, PROBE_TIMEOUT) as response:
            body = response.read(MAX_PLAYLIST)
            final = response.geturl()
        text = body.decode("utf-8", "replace").lstrip("\ufeff \r\n\t")
        if not text.startswith("#EXTM3U"):
            raise ValueError("no es una lista m3u8")
        return text, final

    def _probe(self, target: dict[str, Any]) -> list[Marker]:
        """Markers in the playlist (and its first variant) of one stream, and in a segment if asked to."""
        headers = target["headers"]
        text, base = self._read_playlist(target["url"], headers)
        markers = scan_playlist(text)
        if "#EXT-X-STREAM-INF" in text:
            lines = text.splitlines()
            index = next(i for i, line in enumerate(lines) if line.startswith("#EXT-X-STREAM-INF"))
            variant = next(
                (line.strip() for line in lines[index + 1 :] if line.strip() and not line.startswith("#")), ""
            )
            if not variant:
                raise ValueError("lista maestra sin variantes")
            text, base = self._read_playlist(urllib.parse.urljoin(base, variant), headers)
            markers += scan_playlist(text)
        if self.config.scte_inspect_segments:
            segments = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
            if segments:
                markers += self._inspect_segment(urllib.parse.urljoin(base, segments[-1]), headers)
        return markers

    def _inspect_segment(self, url: str, headers: dict[str, str]) -> list[Marker]:
        asked = {**headers, "Range": f"bytes=0-{SAMPLE_BYTES - 1}"}
        with self.net.open(url, asked, False, PROBE_TIMEOUT) as response:
            return segment_markers(response.read(SAMPLE_BYTES))

    def _scan_one(self, target: dict[str, Any]) -> None:
        if self._stop.is_set():  # the pool's threads keep the process alive until its queue is empty: empty it
            return
        try:
            markers = self._probe(target)
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
            with self._lock:
                self._state["errors"] += 1
                self._state["error"] = f"{target['name']}: {type(exc).__name__}"
        else:
            self._record((target["id"], target["name"]), markers, "exploración", target["url"])
        finally:
            with self._lock:
                self._state["done"] += 1

    def scan(self) -> bool:
        """Look at every target once. Returns ``False`` if a scan was already running."""
        if not self._scan_lock.acquire(blocking=False):
            return False
        try:
            targets = self._targets()
            with self._lock:
                self._state.update(running=True, done=0, total=len(targets), errors=0, error="")
            try:
                with ThreadPoolExecutor(SCAN_WORKERS) as pool:
                    list(pool.map(self._scan_one, targets))
            finally:
                with self._lock:
                    self._state.update(running=False, last=int(time.time()))
            return True
        finally:
            self._scan_lock.release()
