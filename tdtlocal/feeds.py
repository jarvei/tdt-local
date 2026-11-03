"""Synchronisation of the TV and radio lists from GitHub."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
from collections.abc import Callable
from typing import Any

from . import tdtspain
from .config import Config
from .net import Net
from .parser import parse_markdown
from .store import Store

log = logging.getLogger(__name__)

MAX_MARKDOWN = 8_000_000
TICK_SECONDS = 30  # how often the background loop re-reads the settings
RETRY_SECONDS = 900
# kind -> (Store attribute with the channels, Store attribute with the metadata, minimum plausible size)
FEEDS = {
    "tv": ("channels", "meta", 50),
    "radio": ("radios", "radio_meta", 30),
}


class FeedSyncer:
    """Downloads ``TELEVISION.md`` and ``RADIO.md`` (conditional requests via ETag) and stores the result."""

    def __init__(self, config: Config, store: Store, net: Net) -> None:
        self.config = config
        self.store = store
        self.net = net
        self._lock = threading.Lock()
        self._refresh_requested = False

    def _url(self, kind: str) -> str:
        return self.config.tv_source if kind == "tv" else self.config.radio_source

    def _sync_one(self, kind: str, force: bool) -> dict[str, Any]:
        attr, meta_attr, minimum = FEEDS[kind]
        store = self.store
        meta = getattr(store, meta_attr)
        have = getattr(store, attr)
        headers = {}
        if meta.get("etag") and have and not force:
            headers["If-None-Match"] = meta["etag"]
        now = int(time.time())
        try:
            body, response_headers = self.net.fetch_trusted(self._url(kind), headers, limit=MAX_MARKDOWN)
            if len(body) > MAX_MARKDOWN:
                raise ValueError("el fichero es demasiado grande")
            if kind == "tv" and tdtspain.looks_like(body):
                payload = json.loads(body.decode("utf-8", "replace"))
                channels = tdtspain.parse_catalog(payload)
            else:
                channels = parse_markdown(body.decode("utf-8", "replace"), kind)
            if len(channels) < minimum:
                raise ValueError(f"solo {len(channels)} elementos: formato inesperado, se conserva la lista anterior")
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                with store.lock:
                    meta.update(checked=now, error=None)
                store.save_feed(kind)
                return {"changed": False, "count": len(have)}
            with store.lock:
                meta["error"] = f"HTTP {exc.code} al descargar la lista"
            raise
        except Exception as exc:
            with store.lock:
                meta["error"] = f"{type(exc).__name__}: {exc}"
            raise
        with store.lock:
            changed = channels != have
            setattr(store, attr, channels)
            meta.update(checked=now, etag=response_headers.get("ETag"), error=None)
            if changed or not meta.get("updated"):
                meta["updated"] = now
        store.rebuild_allowlists()
        store.save_feed(kind)
        return {"changed": changed, "count": len(channels)}

    def sync(self, force: bool = False) -> dict[str, Any]:
        """Update TV, then radio. A radio failure is only logged: TV is the main list and raises."""
        with self._lock:
            result: dict[str, Any] | None = None
            tv_error: Exception | None = None
            try:
                result = self._sync_one("tv", force)
            except Exception as exc:
                tv_error = exc
            try:
                radio = self._sync_one("radio", force)
                if result is not None:
                    result["radio"] = radio["count"]
            except Exception as exc:
                log.warning("no se pudo actualizar la lista de radios: %s", exc)
            if tv_error:
                raise tv_error
            if result is None:  # unreachable: tv_error would have been raised
                raise RuntimeError("la sincronización no produjo resultado")
            return result

    def refresh_soon(self) -> None:
        """Ask the background loop to download the lists at its next tick (the sources changed)."""
        self._refresh_requested = True

    def run(self, stop: threading.Event, on_synced: Callable[[], None] | None = None) -> None:
        """Background loop: refresh every ``update_hours`` (read each round, so a change applies at once)."""
        cached = bool(self.store.channels and self.store.radios)  # skip the first download only if both are cached
        last = float(self.store.meta.get("checked") or 0) if cached else 0.0
        retry_at = 0.0
        while True:
            now = time.time()
            interval = self.config.update_seconds
            forced = self._refresh_requested
            if forced or now >= (retry_at or last + interval):
                self._refresh_requested = False
                try:
                    result = self.sync(force=forced)
                    log.info("lista %s: %d canales", "actualizada" if result["changed"] else "al día", result["count"])
                    last, retry_at = now, 0.0
                    if on_synced:
                        on_synced()
                except Exception as exc:
                    log.warning("no se pudo actualizar la lista: %s", exc)
                    retry_at = now + min(interval, RETRY_SECONDS)
            if stop.wait(TICK_SECONDS):
                return
