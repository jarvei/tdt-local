"""ID3v2 frames: the ``admode`` text Mediaset's DAI leaves in ``X-MESSAGE-DATA``, and SCTE-35 ``PRIV``.

Ported from ``~/Proyectos/tdt-datos/tdtdatos/teletexto.py`` (only what the ad watcher reads).
"""

from __future__ import annotations

from typing import Any

from . import scte35


def _text(data: bytes, encoding: int) -> str:
    if encoding == 0:
        text = data.decode("latin-1", "replace")
    elif encoding == 1:
        text = data.decode("utf-16", "replace")
    elif encoding == 2:
        text = data.decode("utf-16-be", "replace")
    else:
        text = data.decode("utf-8", "replace")
    return text.replace("\x00", " ").strip()


def analyze(payload: bytes) -> list[dict[str, Any]]:
    """Text frames and SCTE-35 ``PRIV`` frames of an ID3v2 tag (raw or inside a PES packet)."""
    body = payload
    start = body.find(b"ID3")
    if start < 0 or start + 10 > len(body):
        return []
    header = body[start : start + 10]
    version = header[3]
    size = ((header[6] & 0x7F) << 21) | ((header[7] & 0x7F) << 14) | ((header[8] & 0x7F) << 7) | (header[9] & 0x7F)
    data = body[start + 10 : start + 10 + size]
    frames: list[dict[str, Any]] = []
    i = 0
    while i + 10 <= len(data):
        ident = data[i : i + 4]
        if not ident.strip(b"\x00"):
            break
        if version == 4:
            length = (
                ((data[i + 4] & 0x7F) << 21)
                | ((data[i + 5] & 0x7F) << 14)
                | ((data[i + 6] & 0x7F) << 7)
                | (data[i + 7] & 0x7F)
            )
        elif version == 3:
            length = int.from_bytes(data[i + 4 : i + 8], "big")
        else:
            length = (data[i + 4] << 16) | (data[i + 5] << 8) | data[i + 6]
        if length <= 0 or i + 10 + length > len(data):
            break
        content = data[i + 10 : i + 10 + length]
        try:
            key = ident.decode("latin-1")
        except UnicodeDecodeError:
            break
        if key == "PRIV":
            cut = content.find(b"\x00")
            if cut >= 0:
                owner = content[:cut].decode("latin-1", "replace")
                body_priv = content[cut + 1 :]
                if "scte35" in owner.lower() and body_priv:
                    try:
                        splice = scte35.parse(body_priv)
                    except (IndexError, ValueError):
                        splice = None
                    if splice:
                        frames.append({"frame": "PRIV", "owner": owner, "scte35": splice})
        elif key.startswith("T") and content:
            frames.append({"frame": key, "value": _text(content[1:], content[0])[:300]})
        i += 10 + length
    return frames


def admode(value: str | None) -> str | None:
    """``out_<ms>`` / ``in`` inside a base64 ``X-MESSAGE-DATA`` attribute, or ``None``."""
    import base64

    if not value:
        return None
    try:
        raw = base64.b64decode(value, validate=False)
    except (ValueError, TypeError):
        return None
    text = raw.decode("latin-1", "replace")
    index = text.find("admode")
    if index < 0:
        return None
    j = index + len("admode")
    while j < len(text) and ord(text[j]) < 32:
        j += 1
    k = j
    while k < len(text) and ord(text[k]) >= 32:
        k += 1
    return text[j:k] if k > j else None
