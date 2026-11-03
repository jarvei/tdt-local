"""Live ad-break watcher: follows the media playlist of one channel and keeps its break state.

The rules are the ones measured in ``~/Proyectos/tdt-datos`` (which tags count as a break, the
``admode`` fallback, the DASH ``EventStream``) and the timing is the one the TDT Spain app's
``AdMute.java`` uses: the playlist runs ahead of the picture, so the state is evaluated as of
``now - lag``; a break announced with a duration is kept for that duration plus a safety margin
unless its end tag arrives first; Mediaset's DAI may close its break before the broadcast does.

The engine only *decides*; ``scte_watch`` publishes the changes and the page mutes the player.
"""

from __future__ import annotations

import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Callable

from . import dash, hls, id3, scte35

LAG_S = 18.0  # Atresmedia: the player is ~18 s behind the live edge (AdMute's LAG_MS)
MEDIASET_LAG_S = 0.0  # the DAI session already delivers the time left
MARGIN_S = 18.0  # the splice duration usually falls short: the CUE-IN is the good close
MEDIASET_MARGIN_S = 5.0
MAX_BREAK_S = 900.0
MAX_SEEN = 2000  # markers remembered to avoid reporting them again; the oldest are dropped
RETRY_MAX_S = 60.0  # backoff cap when the playlist or the session cannot be read
INTERVAL_S = 2.0
MEDIASET_INTERVAL_S = 1.0
SESSION_S = 240.0  # Mediaset's DAI manifest token lives ~5 min: renew it before it expires
MAX_PLAYLIST = 4_000_000

STARTS = {"SCTE35-OUT", "CUE-OUT", "ID3-AD", "INTERSTITIAL", "SCTE35-BREAK-START"}
ENDS = {"SCTE35-IN", "CUE-IN", "ID3-IN", "SCTE35-BREAK-END", "SCTE35-PROGRAM"}

Event = dict[str, Any]


def _iso(ts: float | None = None) -> str:
    """UTC, the same time base as the ``START-DATE`` of the markers."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts if ts is not None else time.time()))


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    text = str(iso).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:  # no zone: the markers are UTC
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _number(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _daterange_class(event: dict[str, Any]) -> str | None:
    """An interstitial, or the ``admode`` ID3 Mediaset leaves in ``X-MESSAGE-DATA``; anything else is noise."""
    css_class = str(event.get("class") or "").lower()
    if "interstitial" in css_class or event.get("asset_uri") or event.get("asset_list"):
        return "INTERSTITIAL"
    mode = id3.admode(event.get("message_data"))
    if mode:
        if mode == "in":
            return "ID3-IN"
        if mode.startswith("out"):
            return "ID3-AD"
    return None


def normalize(manifest: dict[str, Any], margin: float, mediaset: bool) -> list[Event]:
    """The break markers of a playlist, in file order, with their start/duration when they say."""
    out: list[Event] = []
    for raw in manifest.get("events", []):
        kind = raw.get("type")
        css_class = "DATERANGE"
        scte: dict[str, Any] | None = None
        duration: float | None = None
        start: str | None = None
        end: str | None = None
        event_id = None
        if kind == "daterange":
            out_tag, in_tag, command = raw.get("scte35_out"), raw.get("scte35_in"), raw.get("scte35_cmd")
            if out_tag or in_tag:
                css_class = "SCTE35-OUT" if out_tag else "SCTE35-IN"
                scte = _scte(out_tag or in_tag)
            elif command:
                scte = _scte(command)
                css_class = _command_class(scte)
            else:
                css_class = _daterange_class(raw) or ""
                if not css_class:
                    continue
                if css_class == "ID3-AD":
                    mode = id3.admode(raw.get("message_data")) or ""
                    rest = _number(mode.split("_", 1)[1]) if "_" in mode else None
                    if rest and rest > 0:
                        duration = rest / 1000.0
            duration = _number(raw.get("duration")) or duration
            if duration is not None and duration <= 0:
                duration = None
            if duration is None and scte:
                duration = _number(scte.get("duration_s"))
            start = raw.get("start") or None
            end = raw.get("end") or None
            if not end and start and duration:
                base = _epoch(start)
                end = _iso(base + duration) if base else None
            event_id = (scte or {}).get("event") or raw.get("id")
        elif kind == "cue-out":
            css_class = "CUE-OUT"
            duration = _number(raw.get("value"))
            if duration is not None and duration <= 0:
                duration = None
        elif kind == "cue-in":
            css_class = "CUE-IN"
        elif kind == "scte35":
            scte = _scte(raw.get("value"))
            css_class = _command_class(scte)
            if scte:
                duration = _number(scte.get("duration_s"))
                event_id = scte.get("event")
        else:
            continue
        if css_class in STARTS and duration:
            duration += margin
        detail = _detail(css_class, scte, duration)
        out.append(
            {
                "class": css_class,
                "type": kind,
                "id": raw.get("id"),
                "start": start,
                "end": end,
                "duration": duration,
                "event_id": event_id,
                "detail": detail,
                "raw": str(
                    raw.get("scte35_out") or raw.get("scte35_in") or raw.get("scte35_cmd") or raw.get("value") or ""
                )[:200],
                "key": (css_class, raw.get("id"), start, end, duration, event_id),
                "mediaset": mediaset,
            }
        )
    return out


def _scte(value: str | None) -> dict[str, Any] | None:
    return scte35.from_tag(value)


def _command_class(scte: dict[str, Any] | None) -> str:
    """What a command tag means: the segmentation descriptor decides, not the tag's name.

    Atresmedia sends ``time_signal`` with ``segmentation_type`` 0x22 (break start) and 0x23 (break end)
    instead of a ``splice_insert``: reading it as a plain ``SCTE35-CMD`` would miss those breaks.
    """
    if not scte:
        return "SCTE35-CMD"
    if scte.get("command") == 0x05:
        return "SCTE35-OUT" if scte.get("out_of_network") else "SCTE35-IN"
    kind = scte35.segmentation_info(scte).get("type")
    if kind in scte35.BREAK_START:
        return "SCTE35-BREAK-START"
    if kind in scte35.BREAK_END:
        return "SCTE35-BREAK-END"
    if kind == 0x10:
        return "SCTE35-PROGRAM"
    if kind == 0x11:
        return "SCTE35-PROGRAM-END"
    return "SCTE35-CMD"


def _detail(css_class: str, scte: dict[str, Any] | None, duration: float | None) -> str:
    if scte:
        parts = [scte.get("command_name", "SCTE-35")]
        if scte.get("event") is not None:
            parts.append(f"evento {scte['event']}")
        if scte.get("duration_s"):
            parts.append(f"{scte['duration_s']:.0f} s")
        if scte.get("out_of_network"):
            parts.append("fuera de red")
        return " · ".join(parts)
    if css_class.startswith("ID3"):
        return "ID3 admode"
    if css_class == "INTERSTITIAL":
        return "intersticial HLS"
    return f"{duration:.0f} s" if duration else ""


def state_of(events: list[Event], now: float, mediaset: bool = False) -> tuple[str, str | None, int | None]:
    """``(state, since, seconds left)`` from the last marker that applies, as of ``now``."""
    state, since, left = "programa", None, None
    for event in events:
        css_class = event.get("class")
        if css_class in STARTS:
            base = _epoch(event.get("start")) or event.get("seen") or now
            if base > now:
                continue  # it has not reached the picture yet: the playlist runs ahead of it
            duration = event.get("duration")
            if duration and base + duration <= now:
                state, since, left = "programa", _iso(base + duration), None
                continue
            state = "publicidad"
            since = _iso(base)
            left = round(base + duration - now) if duration else None
        elif css_class in ENDS:
            if mediaset and state == "publicidad" and left and left > 0:
                continue  # the DAI closed its break before the broadcast did (earlyMediasetIn)
            state, since, left = "programa", _iso(event.get("seen") or now), None
    return state, since, left


def last_segment(playlist: str) -> str:
    """The last media segment a playlist points at (absolute or relative)."""
    found = ""
    for line in playlist.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            found = line
    return found


class Vigilance:
    """Polls one channel and reports its state. ``on_change(state, since, left, event)`` is optional."""

    def __init__(
        self,
        channel_id: str,
        name: str,
        url: str,
        headers: dict[str, str] | None,
        net: Any,
        *,
        resolver: Callable[[], dict[str, Any]] | None = None,
        dash_url: str | None = None,
        lag: float = LAG_S,
        margin: float = MARGIN_S,
        interval: float = INTERVAL_S,
        on_change: Callable[[str, str | None, int | None, Event | None, str], None] | None = None,
        stop: threading.Event | None = None,
        mediaset: bool = False,
    ) -> None:
        self.channel_id = channel_id
        self.name = name
        self.url = url
        self.headers = dict(headers or {})
        self.net = net
        self.resolver = resolver
        self.dash_url = dash_url
        self.lag = lag
        self.margin = margin
        self.interval = interval
        self.on_change = on_change
        self.stop = stop or threading.Event()
        # Closing one watcher must not stop the others: the `stop` above is the application's, shared.
        self._closed = threading.Event()
        self.mediaset = mediaset
        self.variant: str | None = None
        self.resolved_at = time.time()
        self.state = "desconocido"
        self.since: str | None = None
        self.left: int | None = None
        self.events: list[Event] = []
        self.seen: dict[tuple, float] = {}  # marker key -> when it was first seen
        self.errors = 0
        self.last_error = ""
        self.last_read: str | None = None
        self.started_at = _iso()
        self._lock = threading.Lock()  # state and events are read from the server thread
        self._fail_count = 0
        self._next_try = 0.0
        self._thread = threading.Thread(target=self._loop, name=f"ads-{channel_id}", daemon=True)

    # ---- life cycle
    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._closed.set()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    # ---- sampling
    def _loop(self) -> None:
        while not self._closed.is_set() and not self.stop.is_set():
            wait = self.interval
            if self._next_try > time.time():
                wait = min(self._next_try - time.time(), self.interval)
            else:
                try:
                    self._step()
                    self.errors = 0
                    self._fail_count = 0
                    self._next_try = 0.0
                except Exception as exc:
                    self.errors += 1
                    self._fail_count += 1
                    # 5, 10, 20, 40, 60 s: a fallen origin is not hammered every two seconds
                    self._next_try = time.time() + min(RETRY_MAX_S, 5.0 * (2 ** min(self._fail_count - 1, 4)))
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    if self.resolver:  # a token may have expired: renew before the next try
                        self.resolved_at = 0.0
                        self.variant = None
            self._closed.wait(wait)

    def _renew(self) -> None:
        if self.resolver is None:
            return
        out = self.resolver()
        if out.get("url"):
            self.url = out["url"]
            self.headers = dict(out.get("headers") or {})
            self.variant = None
            self.resolved_at = time.time()

    def _read(self, url: str) -> str:
        with self.net.open(url, self.headers, False, 10) as response:
            return response.read(MAX_PLAYLIST).decode("utf-8", "replace").lstrip("\ufeff \r\n\t")

    def _step(self) -> None:
        if not self.url:
            self._renew()
        elif self.resolver and self.state != "publicidad" and time.time() - self.resolved_at > SESSION_S:
            self._renew()  # a new DAI session, but never in the middle of a break
        if not self.url:
            return
        if self.variant is None:
            text = self._read(self.url)
            manifest = hls.summarize(text, self.url)
            best = _best_variant(manifest, self.url)
            self.variant = urllib.parse.urljoin(self.url, best)
            if self.variant == self.url:
                self._process(text, manifest)
            else:
                return  # the next tick reads the media playlist
        text = self._read(self.variant)
        self._process(text, hls.summarize(text, self.variant))
        self.last_read = _iso()
        if self.dash_url:
            try:
                self._process_dash(self._read(self.dash_url))
            except Exception as exc:
                self.last_error = f"DASH {type(exc).__name__}: {exc}"

    def _process(self, text: str, manifest: dict[str, Any]) -> None:
        normalized = normalize(manifest, self.margin, self.mediaset)
        self._remember(normalized)
        if not normalized and self.mediaset:
            # A DAI session can start in the middle of a break: the pod segments are the only sign left.
            segment = last_segment(text)
            if "dai.google.com" in segment or "/linear/pods/" in segment:
                now = time.time()
                if self.state != "publicidad":
                    self._change("publicidad", _iso(now), None, None)
                return
        self._update()

    def _remember(self, events: list[Event]) -> None:
        """Append the markers not seen yet, under the lock, and keep both collections bounded."""
        now = time.time()
        with self._lock:
            for event in events:
                if event["key"] in self.seen:
                    continue
                self.seen[event["key"]] = now
                event["seen"] = now
                self.events.append(event)
            if len(self.events) > 500:
                del self.events[: len(self.events) - 500]
            self._trim_seen()

    def _trim_seen(self) -> None:
        """Drop the oldest keys when there are too many; the ones still in ``events`` are kept."""
        if len(self.seen) <= MAX_SEEN:
            return
        keep = {event["key"] for event in self.events}
        excess = len(self.seen) - MAX_SEEN
        for key, _ in sorted(self.seen.items(), key=lambda item: item[1]):
            if excess <= 0:
                break
            if key not in keep:
                del self.seen[key]
                excess -= 1

    def _process_dash(self, text: str) -> None:
        manifest = dash.summarize(text, self.dash_url or "")
        events: list[Event] = []
        for raw in manifest.get("events", []):
            seg_type = raw.get("segmentation_type")
            if seg_type == 0x22:
                css_class = "SCTE35-BREAK-START"
            elif seg_type == 0x23:
                css_class = "SCTE35-BREAK-END"
            elif seg_type == 0x10:
                css_class = "SCTE35-PROGRAM"
            elif seg_type == 0x11:
                css_class = "SCTE35-PROGRAM-END"
            elif raw.get("command") == "splice_insert":
                css_class = "SCTE35-OUT" if raw.get("out_of_network") else "SCTE35-IN"
            else:
                css_class = "SCTE35-CMD"
            key = ("dash", raw.get("id"), raw.get("presentation_time"), raw.get("command"), seg_type)
            if key in self.seen:
                continue
            events.append(
                {
                    "class": css_class,
                    "type": "dash",
                    "id": raw.get("id"),
                    "start": None,
                    "end": None,
                    "duration": raw.get("duration_s"),
                    "event_id": raw.get("event") or raw.get("id"),
                    "detail": raw.get("segmentation_name") or raw.get("command") or "SCTE-35",
                    "raw": str(raw.get("raw") or "")[:200],
                    "key": key,
                    "mediaset": self.mediaset,
                }
            )
        if events:
            self._remember(events)
            self._update()

    def _update(self) -> None:
        now = time.time()
        with self._lock:
            state, since = self.state, self.since
            events = list(self.events)
        if state == "publicidad" and since:
            started = _epoch(since) or now
            if now - started > MAX_BREAK_S:
                self._change("programa", _iso(now), None, None)
                return
        # The playlist runs `lag` seconds ahead of the picture: the state that matters is the one the
        # live edge had `lag` seconds ago, which is what the viewer is seeing now.
        new_state, new_since, left = state_of(events, now - self.lag, self.mediaset)
        with self._lock:
            changed = new_state != self.state or (new_state == "publicidad" and left != self.left)
        if changed:
            self._change(new_state, new_since, left, None)

    def _change(self, state: str, since: str | None, left: int | None, event: Event | None) -> None:
        with self._lock:
            previous = self.state
            self.state, self.since, self.left = state, since, left
        if self.on_change and state != previous:
            self.on_change(state, since, left, event, previous)

    # ---- views
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            events = [
                {k: v for k, v in event.items() if k not in ("key", "seen", "mediaset")} for event in self.events[-10:]
            ]
            return {
                "channel": self.channel_id,
                "name": self.name,
                "state": self.state,
                "alive": self.alive,
                "stopped": self._closed.is_set() or self.stop.is_set(),
                "since": self.since,
                "left": self.left,
                "lag": self.lag,
                "url": self.url,
                "variant": self.variant,
                "dash": self.dash_url,
                "started": self.started_at,
                "read": self.last_read,
                "errors": self.errors,
                "error": self.last_error,
                "events": events,
            }


def _best_variant(manifest: dict[str, Any], url: str) -> str:
    """The highest-bitrate video variant of a master playlist; a media playlist is itself."""
    if manifest.get("type") == "media":
        return url
    variants = manifest.get("variants") or []
    if not variants:
        return url
    with_video = [v for v in variants if v.get("resolution")]
    best = max(with_video or variants, key=lambda v: v.get("bandwidth") or 0)
    return best.get("uri") or url
