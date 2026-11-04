"""Wiring of all services and their background threads."""

from __future__ import annotations

import threading
import time
from typing import Any

from . import __version__, ads, tdtspain, u7d
from .ai import Assistant
from .config import Config
from .epg import EpgSyncer
from .feeds import FeedSyncer
from .health import HealthChecker
from .logos import LogoCache
from .net import Net
from .scte_watch import ScteWatcher
from .settings import BY_NAME, SettingsStore
from .store import Store, channel_ok, source_ok
from .stt import Transcriber
from .u7d import U7d
from .whisper_models import ModelDownloader
from .youtube import YouTubeLive

RESOLVE_TTL = 120.0  # a resolved URL is reused for this long: the DAI/Atresplayer tokens last minutes


class App:
    """Owns the services. The HTTP handler reaches everything through ``server.app``."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.settings = SettingsStore(config)
        self.settings.load()  # before the services: they read the configuration when they are created
        self.stop = threading.Event()
        self.net = Net(allow_private=config.allow_private)
        self.store = Store(config, self.net)
        self.feeds = FeedSyncer(config, self.store, self.net)
        self.epg = EpgSyncer(config, self.store, self.net)
        self.health = HealthChecker(self.store, self.net, stop=self.stop)
        self.u7d = U7d(self.net, self._allow_cover)
        self.youtube = YouTubeLive(self.net)
        self.scte = ScteWatcher(config, self.store, self.net)
        self.assistant = Assistant(config, self.store)
        self.transcriber = Transcriber(config)
        self.models = ModelDownloader(config, self.net)
        self.logos = LogoCache(self.store, self.net, config.data_dir / "logos")
        # Resolved URLs (a play and the ad watcher may ask for the same channel at once) and one lock
        # per channel so only the first one resolves; the rest take the cached result.
        self._resolved: dict[str, tuple[float, dict[str, Any]]] = {}
        self._resolve_locks: dict[str, threading.Lock] = {}
        self._resolve_guard = threading.Lock()

    def start_background(self) -> None:
        """Load the cache and start the refresh threads."""
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.store.load()
        self.scte.load()
        threading.Thread(target=self.feeds.run, args=(self.stop, self._after_sync), daemon=True, name="feeds").start()
        threading.Thread(target=self.epg.run, args=(self.stop,), daemon=True, name="epg").start()
        threading.Thread(target=self.scte.run, args=(self.stop,), daemon=True, name="scte").start()

    def _after_sync(self) -> None:
        with self._resolve_guard:  # the catalogue may have changed: old resolved URLs no longer apply
            self._resolved.clear()
        if self.config.auto_check:  # read now, not at start-up: it can be changed in the settings
            self.health.start()

    def apply_settings(self, changed: list[str]) -> None:
        """Tell the services whose behaviour depends on the settings that were just changed."""
        affected = {service for name in changed for service in BY_NAME[name].affects}
        if "stt" in affected:
            self.transcriber.reconfigure()
        if "epg" in affected:
            self.epg.refresh_soon()
        if "feeds" in affected:
            self.feeds.refresh_soon()
        if "scte" in affected:
            self.scte.reconfigure()

    def close(self) -> None:
        self.stop.set()
        self.transcriber.close()

    def allow_covers(self, urls: Any) -> int:
        """Let the logo proxy fetch the covers a page names (see ``u7d.cover_ok``). Returns how many it took."""
        taken = 0
        for url in (urls if isinstance(urls, list) else [])[: u7d.MAX_COVERS]:
            if u7d.cover_ok(url):
                self._allow_cover(url)
                taken += 1
        return taken

    def _allow_cover(self, url: str) -> None:
        with self.store.lock:
            self.store.cover_urls.add(url)

    def channel_by_id(self, cid: str) -> dict[str, Any] | None:
        with self.store.lock:
            return next((ch for ch in self.store.all_channels() if ch["id"] == cid), None)

    def watch_ads(self, cid: str) -> dict[str, Any]:
        """Follow one channel with the ad-break watcher (see ``scte_watch``)."""
        channel = self.channel_by_id(cid)
        if channel is None:
            raise ValueError("canal desconocido")
        if not self.config.scte_enabled:
            return {"ok": False, "error": "la vigilancia está desactivada"}

        def resolve() -> dict[str, Any]:
            out = self.resolve_channel(cid)
            return {"url": out["url"], "headers": out.get("headers") or {}}

        return self.scte.watch(channel, resolve)

    def resolve_channel(self, cid: str) -> dict[str, Any]:
        """The live URL of a channel whose recipe is followed at play time (TDT Spain's list)."""
        with self._resolve_guard:
            cached = self._resolved.get(cid)
            lock = self._resolve_locks.setdefault(cid, threading.Lock())
        if cached and time.time() - cached[0] < RESOLVE_TTL:
            return dict(cached[1])
        with lock:
            with self._resolve_guard:  # another request may have resolved it while we waited
                cached = self._resolved.get(cid)
            if cached and time.time() - cached[0] < RESOLVE_TTL:
                return dict(cached[1])
            channel = self.channel_by_id(cid)
            if channel is None:
                raise ValueError("canal desconocido")
            out = tdtspain.resolve(channel, self.net)
            self.net.allow(out["url"])
            with self.store.lock:
                self.store.extra_headers[cid] = dict(out.get("headers") or {})
            with self._resolve_guard:
                self._resolved[cid] = (time.time(), dict(out))
            return out

    def channels_payload(self) -> dict[str, Any]:
        """The JSON behind ``/api/channels``: every channel with its health, plus list metadata."""
        store = self.store
        with store.lock:
            channels, status = store.all_channels(), store.status
            meta, health = dict(store.meta), dict(store.health)
            radio = {k: store.radio_meta.get(k) for k in ("checked", "updated", "error")}
            out = []
            for ch in channels:
                fmts = ch.get("fmts") or []
                sources = [
                    {"u": u, "ok": source_ok(ch, u, status), "f": fmts[i] if i < len(fmts) else ""}
                    for i, u in enumerate(ch["sources"])
                ]
                item = {k: v for k, v in ch.items() if k not in ("fmts", "headers", "tdtspain")}
                item["sources"], item["ok"] = sources, channel_ok(ch, status)
                item["kind"] = item.get("kind") or "tv"
                out.append(item)
        meta.pop("etag", None)
        meta["radio"] = radio
        meta["ai"] = self.config.ai_enabled
        meta["version"] = __version__
        meta["quit"] = self.config.allow_quit
        meta["stt"] = self.transcriber.available()
        meta["scte"] = self.config.scte_enabled
        meta["ads"] = ads.payload(self.config)
        return {"meta": meta, "health": health, "channels": out}
