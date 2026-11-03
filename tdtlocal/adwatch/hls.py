"""HLS manifests: variants, audio/subtitle tracks and data markers.

Ported from ``~/Proyectos/tdt-datos/tdtdatos/hls.py``: the same parser the ad watcher there uses.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin


def _attributes(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i] in " ,":
            i += 1
        if i >= n:
            break
        j = text.find("=", i)
        if j < 0:
            break
        key = text[i:j].strip()
        i = j + 1
        if i < n and text[i] == '"':
            k = text.find('"', i + 1)
            if k < 0:
                value = text[i + 1 :]
                i = n
            else:
                value = text[i + 1 : k]
                i = k + 1
        else:
            k = text.find(",", i)
            if k < 0:
                value = text[i:].strip()
                i = n
            else:
                value = text[i:k].strip()
                i = k
        if key:
            out[key] = value
    return out


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _resolution(value: str | None) -> str | None:
    return value if value and "x" in value else None


def _track(kind: str, attrs: dict[str, str], base: str) -> dict[str, Any]:
    uri = attrs.get("URI")
    return {
        "kind": kind,
        "group": attrs.get("GROUP-ID", ""),
        "name": attrs.get("NAME", ""),
        "language": attrs.get("LANGUAGE", ""),
        "uri": urljoin(base, uri) if uri else None,
        "default": attrs.get("DEFAULT", "NO").upper() == "YES",
        "autoselect": attrs.get("AUTOSELECT", "NO").upper() == "YES",
        "forced": attrs.get("FORCED", "NO").upper() == "YES",
        "channels": attrs.get("CHANNELS"),
        "characteristics": attrs.get("CHARACTERISTICS", ""),
        "instream_id": attrs.get("INSTREAM-ID"),
    }


EMPTY = {"type": "unknown", "variants": [], "tracks": [], "data": [], "events": []}


def summarize(text: str, url: str) -> dict[str, Any]:
    """Read a master or a media playlist and return its tracks, variants and markers."""
    base = url
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        return dict(EMPTY)

    tracks: list[dict[str, Any]] = []
    variants: list[dict[str, Any]] = []
    iframe: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    segments = 0
    target = None
    media_sequence = None
    pending: dict[str, str] | None = None

    for line in lines:
        if line.startswith("#EXT-X-MEDIA:"):
            attrs = _attributes(line.split(":", 1)[1])
            tracks.append(_track(attrs.get("TYPE", ""), attrs, base))
        elif line.startswith("#EXT-X-STREAM-INF:"):
            pending = _attributes(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-I-FRAME-STREAM-INF:"):
            attrs = _attributes(line.split(":", 1)[1])
            iframe.append(
                {
                    "uri": urljoin(base, attrs.get("URI", "")),
                    "bandwidth": _int(attrs.get("BANDWIDTH")),
                    "codecs": attrs.get("CODECS", ""),
                    "resolution": _resolution(attrs.get("RESOLUTION")),
                }
            )
        elif line.startswith("#EXT-X-DATERANGE:"):
            attrs = _attributes(line.split(":", 1)[1])
            events.append(
                {
                    "type": "daterange",
                    "id": attrs.get("ID", ""),
                    "class": attrs.get("CLASS", ""),
                    "start": attrs.get("START-DATE", ""),
                    "end": attrs.get("END-DATE", ""),
                    "duration": attrs.get("DURATION", attrs.get("PLANNED-DURATION", "")),
                    "scte35_out": attrs.get("SCTE35-OUT"),
                    "scte35_in": attrs.get("SCTE35-IN"),
                    "scte35_cmd": attrs.get("SCTE35-CMD"),
                    "cue": attrs.get("CUE", ""),
                    "message_data": attrs.get("X-MESSAGE-DATA"),
                    "asset_uri": attrs.get("X-ASSET-URI"),
                    "asset_list": attrs.get("X-ASSET-LIST"),
                }
            )
        elif line.startswith("#EXT-X-CUE-OUT"):
            events.append({"type": "cue-out", "value": line.split(":", 1)[-1]})
        elif line.startswith("#EXT-X-CUE-IN"):
            events.append({"type": "cue-in", "value": line.split(":", 1)[-1]})
        elif line.startswith("#EXT-X-SCTE35:"):
            events.append({"type": "scte35", "value": line.split(":", 1)[1]})
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            target = _int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            media_sequence = _int(line.split(":", 1)[1])
        elif line.startswith("#EXTINF:"):
            segments += 1
        elif not line.startswith("#") and pending is not None:
            variants.append(
                {
                    "uri": urljoin(base, line),
                    "bandwidth": _int(pending.get("BANDWIDTH")),
                    "average_bandwidth": _int(pending.get("AVERAGE-BANDWIDTH")),
                    "codecs": pending.get("CODECS", ""),
                    "resolution": _resolution(pending.get("RESOLUTION")),
                    "frame_rate": pending.get("FRAME-RATE"),
                    "audio": pending.get("AUDIO", ""),
                    "subtitles": pending.get("SUBTITLES", ""),
                    "closed_captions": pending.get("CLOSED-CAPTIONS", ""),
                    "video_range": pending.get("VIDEO-RANGE", ""),
                }
            )
            pending = None

    data = [t for t in tracks if t["kind"] in ("SUBTITLES", "CLOSED-CAPTIONS")]
    for track in tracks:
        if track["kind"] != "AUDIO":
            continue
        character = track["characteristics"].lower()
        name = track["name"].lower()
        if "describ" in character or "descrip" in name or track["language"] in ("qaa", "ads") or "audio_ads" in name:
            track["accessible"] = True
            data.append(track)

    if variants and not segments:
        kind = "master"
    elif segments:
        kind = "media"
    else:
        kind = "master" if tracks or iframe else "unknown"

    return {
        "type": kind,
        "url": url,
        "target_duration": target,
        "media_sequence": media_sequence,
        "segments": segments,
        "variants": variants,
        "iframe": iframe,
        "tracks": tracks,
        "data": data,
        "events": events,
    }
