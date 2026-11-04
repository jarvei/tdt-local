"""Current live video of a YouTube channel.

YouTube's old ``/embed/live_stream?channel=…`` player no longer starts and shows "Video unavailable". The page
therefore embeds the video id it reads from the channel's public live page, which points at the broadcast on air.
A catalog can also give a fixed ``/live/<id>``: that video is only a snapshot, so the channel's current live wins.
"""

from __future__ import annotations

import re
import threading
import time
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .net import Net

LIVE_PATH = re.compile(r"^/(?:channel/UC[A-Za-z0-9_-]{22}|@[A-Za-z0-9._-]+)/live$")
VIDEO_PATH = re.compile(r"^/live/([\w-]{11})$")
LINK_TAG = re.compile(r"<link\b[^>]*>")
CANONICAL_REL = re.compile(r'rel="canonical"')
CANONICAL_HREF = re.compile(r'href="https://www\.youtube\.com/watch\?v=([\w-]{11})"')
PLAYABILITY = re.compile(r'"playabilityStatus":\{"status":"([A-Z_]+)"(?:[^{}]*?"playableInEmbed":(true|false))?')
VIDEO_DETAILS = re.compile(r'"videoDetails":\{"videoId":"([\w-]{11})".{0,2000}?"isLive":(true|false)', re.S)
VIDEO_CHANNEL = re.compile(r'"videoDetails":\{.{0,10000}?"channelId":"(UC[\w-]{22})"', re.S)
CHANNEL_ID = re.compile(r'"channelId":"(UC[\w-]{22})"')
MAX_PAGE = 4_000_000
CACHE_SECONDS = 60.0


def live_page(url: str) -> str | None:
    """The normalized live page of a YouTube channel, or ``None`` if ``url`` is not one."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None
    path = parts.path.rstrip("/")
    if parts.scheme not in ("http", "https") or host not in ("www.youtube.com", "youtube.com"):
        return None
    if port not in (None, 80, 443):
        return None
    if LIVE_PATH.fullmatch(path) is None:
        return None
    return "https://www.youtube.com" + path + "?hl=en"


def video_page(url: str) -> str | None:
    """The normalized watch page of a direct ``/live/<id>`` URL, or ``None`` if ``url`` is not one."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None
    path = parts.path.rstrip("/")
    if parts.scheme not in ("http", "https") or host not in ("www.youtube.com", "youtube.com"):
        return None
    if port not in (None, 80, 443):
        return None
    match = VIDEO_PATH.fullmatch(path)
    if match is None:
        return None
    return "https://www.youtube.com/watch?v=" + match.group(1) + "&hl=en"


def channel_live(channel: str) -> str:
    """The live page of a channel id."""
    return f"https://www.youtube.com/channel/{channel}/live?hl=en"


def video_channel(page: str) -> str | None:
    """The channel id that owns the video in a watch page, or ``None``."""
    match = VIDEO_CHANNEL.search(page) or CHANNEL_ID.search(page)
    return match.group(1) if match else None


def live_video(page: str) -> str | None:
    """The id of the live video in a channel page, or ``None`` if it is not live or not embeddable."""
    video = None
    for tag in LINK_TAG.finditer(page):
        text = tag.group()
        if CANONICAL_REL.search(text) and (match := CANONICAL_HREF.search(text)):
            video = match.group(1)
            break
    if video is None:
        return None
    details = VIDEO_DETAILS.search(page)
    if details is None or details.group(1) != video or details.group(2) != "true":
        return None
    status = PLAYABILITY.search(page)
    if status is None or status.group(1) != "OK" or status.group(2) == "false":
        return None
    return video


class YouTubeLive:
    """Resolve and briefly cache the live video of a channel."""

    def __init__(self, net: Net) -> None:
        self.net = net
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, str]] = {}

    def resolve(self, url: str) -> str:
        """Return the current live video id. Raises :class:`ValueError` if there is no embeddable live stream."""
        page = live_page(url)
        if page is not None:
            return self._resolve_channel(page)
        watch = video_page(url)
        if watch is None:
            raise ValueError("enlace de YouTube no válido")
        hit = self._cached(watch)
        if hit is not None:
            return hit
        body = self._read(watch)
        direct = live_video(body)
        video = None
        channel = video_channel(body)
        if channel is not None:
            try:
                video = self._resolve_channel(channel_live(channel))
            except ValueError:
                if direct is None:
                    raise
        if video is None:
            video = direct
        if video is None:
            raise ValueError("YouTube no da un directo incrustable para ese vídeo")
        self._remember(watch, video)
        return video

    def _resolve_channel(self, page: str) -> str:
        hit = self._cached(page)
        if hit is not None:
            return hit
        video = live_video(self._read(page))
        if video is None:
            raise ValueError("YouTube no da un directo incrustable para ese canal")
        self._remember(page, video)
        return video

    def _cached(self, key: str) -> str | None:
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
        return None

    def _remember(self, key: str, video: str) -> None:
        with self._lock:
            self._cache[key] = (time.time(), video)

    def _read(self, page: str) -> str:
        with self.net.open(page, {"Accept-Language": "en-US,en;q=0.9"}, True, 20) as response:
            body = response.read(MAX_PAGE + 1)
        if len(body) > MAX_PAGE:
            raise ValueError("la página de YouTube es demasiado grande")
        return body.decode("utf-8", "replace")
