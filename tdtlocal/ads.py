"""What the page may do while an ad break is on: the settings it receives, and what a web panel needs.

The reaction itself (mute, switch channel, show a web) happens in the page, where the player is
(``static/js/adbreak.js``). The server only decides what the page is told, and lets the one web address the
user chose be shown inside the page.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Mapping
from typing import Any

from .config import Config

HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$|^[0-9a-f:]+$")  # urlsplit lower-cases the host
SENT = (
    "ad_flag",
    "ad_audio",
    "ad_lower_percent",
    "ad_screen",
    "ad_channel",
    "ad_web_url",
    "ad_min_seconds",
    "ad_unknown_seconds",
    "ad_on_touch",
)


def web_origin(config: Config) -> str:
    """``scheme://host[:port]`` of the web panel, or ``""`` when no web panel is configured."""
    if not (config.ad_enabled and config.ad_screen == "web" and config.ad_web_url):
        return ""
    try:
        parts = urllib.parse.urlsplit(config.ad_web_url)
        host, port = parts.hostname, parts.port
    except ValueError:
        return ""
    # the origin goes into a header: only a plain host name or address, never anything that could add a directive
    if parts.scheme not in ("http", "https") or not host or not HOST.match(host):
        return ""
    shown = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{shown}" + (f":{port}" if port else "")


def payload(config: Config) -> dict[str, Any] | None:
    """The reaction settings for the page, or ``None`` when there is nothing to react with.

    Breaks are found by the server, which follows the playlist of the channel being watched: the page only gets
    the events. It needs at least one thing to do.
    """
    if not config.ad_enabled:
        return None
    out: dict[str, Any] = {name.removeprefix("ad_"): getattr(config, name) for name in SENT}
    if out["screen"] == "channel" and not out["channel"]:
        out["screen"] = "keep"
    if out["screen"] == "web" and not web_origin(config):
        out["screen"] = "keep"
    if out["audio"] == "keep" and out["screen"] == "keep" and not out["flag"]:
        return None
    return out


def embed_verdict(headers: Mapping[str, str], our_origin: str) -> tuple[bool, str]:
    """Whether a page that answered with these headers can be shown inside ours, and why not.

    ``X-Frame-Options`` and ``frame-ancestors`` are what a site uses to refuse it; the browser enforces them,
    so a refusing site would only show an empty panel.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    option = lowered.get("x-frame-options", "").strip().lower()
    if option.startswith(("deny", "sameorigin")):
        return False, "la web no permite que se muestre dentro de otra página (X-Frame-Options)"
    for policy in lowered.get("content-security-policy", "").split(","):
        for directive in policy.split(";"):
            name, _, value = directive.strip().partition(" ")
            if name.lower() != "frame-ancestors":
                continue
            allowed = value.lower().split()
            if "*" in allowed or our_origin.lower() in allowed or {"http:", "https:"} & set(allowed):
                continue
            return False, "la web no permite que se muestre dentro de otra página (frame-ancestors)"
    return True, ""
