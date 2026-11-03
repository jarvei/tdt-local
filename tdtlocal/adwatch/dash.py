"""DASH manifests: ``EventStream`` SCTE-35 events.

Ported from ``~/Proyectos/tdt-datos/tdtdatos/dash.py``.
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from typing import Any

from . import scte35


def _number(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _events(text: str, url: str) -> dict[str, Any]:
    if "<!entity" in text.lower():
        return {"type": "dash", "url": url, "events": [], "error": "MPD con entidades: rechazado"}
    try:
        root = ET.fromstring(text)  # noqa: S314 - entities rejected above, size capped by the reader
    except ET.ParseError:
        return {"type": "dash", "url": url, "events": [], "error": "MPD inválido"}
    events = []
    periods = 0
    for node in root.iter():
        name = _local(node.tag)
        if name == "Period":
            periods += 1
        if name != "EventStream":
            continue
        scheme = (node.get("schemeIdUri") or "").lower()
        if "scte35" not in scheme:
            continue
        timescale = _number(node.get("timescale")) or 1.0
        for event in node:
            if _local(event.tag) != "Event":
                continue
            binary = next((child for child in event.iter() if _local(child.tag) == "Binary"), None)
            payload = (binary.text or "").strip() if binary is not None else ""
            if not payload:
                continue
            try:
                section = base64.b64decode(payload, validate=True)
                splice = scte35.parse(section)
            except (ValueError, TypeError, IndexError):
                continue
            seg = scte35.segmentation_info(splice)
            ticks = _number(event.get("duration"))
            duration = (ticks / timescale) if ticks is not None else _number(splice.get("duration_s"))
            kind = seg.get("type")
            ad = None
            if splice.get("command") == 0x05 and splice.get("out_of_network"):
                ad = True
            if kind in scte35.BREAK_START:
                ad = True
            elif kind in scte35.BREAK_END:
                ad = False
            events.append(
                {
                    "id": event.get("id"),
                    "presentation_time": _number(event.get("presentationTime")),
                    "duration_s": round(duration, 3) if duration is not None else None,
                    "timescale": timescale,
                    "command": splice.get("command_name"),
                    "event": splice.get("event"),
                    "out_of_network": splice.get("out_of_network"),
                    "segmentation_type": kind,
                    "segmentation_name": seg.get("name"),
                    "ad": ad,
                    "raw": section.hex()[:240],
                }
            )
    events.sort(key=lambda e: (e.get("presentation_time") or 0, e.get("id") or ""))
    return {
        "type": "dash",
        "url": url,
        "periods": periods,
        "publish_time": root.get("publishTime"),
        "events": events,
    }


def summarize(text: str, url: str) -> dict[str, Any]:
    """A DASH manifest with its SCTE-35 ``EventStream`` events."""
    return _events(text, url)
